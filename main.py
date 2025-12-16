#!/usr/bin/env python3


import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import os
import json
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from urllib.parse import quote
import requests

try:
    from typing import TypedDict
except ImportError:
    from typing_extensions import TypedDict

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

KPH_TO_MPS = 1 / 3.6

class WeatherData(TypedDict):
    temperature: float
    feels_like: float
    humidity: float
    pressure: float
    wind_speed: float
    wind_direction: float
    description: str
    source: str
    city: str

class WeatherAPIConfig:
    def __init__(self):
        self.timeout = 15
        self.retry_attempts = 2
        self.cache_ttl = 3600
        self.request_delay = 0.5
        self.max_cache_age_days = 7
        self.rate_limit_per_hour = 100

class FreeWeatherAPI:
    def __init__(self, city: str = "Vilnius", lat: float = 54.6872, lon: float = 25.2797, enable_cache: bool = False):
        self.city = city
        self.latitude = lat
        self.longitude = lon
        self.enable_cache = enable_cache
        
        self.config = WeatherAPIConfig()
        self.weather_api_key = os.getenv('WEATHERAPI_KEY', 'demo')
        if self.weather_api_key == 'demo':
            logger.warning("Using demo WeatherAPI key")
        
        self.cache_dir = Path('.weather_cache')
        if self.enable_cache:
            self.cache_dir.mkdir(exist_ok=True)
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; WeatherApp/1.0)'
        })
        
        self.open_meteo_weather_codes = {
            0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
            45: "Fog", 48: "Depositing rime fog",
            51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
            56: "Light freezing drizzle", 57: "Dense freezing drizzle",
            61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
            66: "Light freezing rain", 67: "Heavy freezing rain",
            71: "Slight snow fall", 73: "Moderate snow fall", 75: "Heavy snow fall",
            77: "Snow grains",
            80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
            85: "Slight snow showers", 86: "Heavy snow showers",
            95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail"
        }
        
        if self.enable_cache:
            self._clean_old_cache()

    def _validate_url(self, url: str) -> bool:
        return bool(url and url.startswith(('http://', 'https://')))

    def _clean_old_cache(self) -> None:
        cutoff_time = time.time() - (self.config.max_cache_age_days * 86400)
        
        for cache_file in self.cache_dir.glob('cache_*.json'):
            try:
                if cache_file.stat().st_mtime < cutoff_time:
                    cache_file.unlink()
                    logger.debug(f"Removed old cache file: {cache_file}")
            except OSError as e:
                logger.warning(f"Failed to remove cache file {cache_file}: {e}")

    def _get_cache_key(self, url: str, params: Dict[str, Any]) -> str:
        if not params:
            return f"cache_{quote(url, safe='')}.json"
        
        sorted_params = sorted(params.items())
        param_hash = hash(frozenset(sorted_params))
        return f"cache_{quote(url, safe='')}_{param_hash}.json"

    def _cache_response(self, cache_file: Path, data: Dict[str, Any]) -> None:
        if not self.enable_cache:
            return
            
        try:
            cache_file.write_text(json.dumps(data, indent=2))
            logger.debug(f"Cached response to {cache_file}")
        except (IOError, TypeError) as e:
            logger.warning(f"Failed to cache response: {e}")

    def _load_cached_response(self, cache_file: Path) -> Optional[Dict[str, Any]]:
        if not self.enable_cache:
            return None
            
        if not cache_file.exists():
            return None
            
        try:
            file_age = time.time() - cache_file.stat().st_mtime
            if file_age < self.config.cache_ttl:
                data = json.loads(cache_file.read_text())
                logger.debug(f"Loaded cached response from {cache_file}")
                return data
        except (IOError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to load cached response: {e}")
            
        return None

    def _make_request(self, url: str, params: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        if not self._validate_url(url):
            logger.error(f"Invalid URL: {url}")
            return None

        cache_file = None
        if self.enable_cache:
            cache_file = self.cache_dir / self._get_cache_key(url, params)
            cached_data = self._load_cached_response(cache_file)
            if cached_data:
                return cached_data

        for attempt in range(self.config.retry_attempts):
            try:
                response = self.session.get(url, params=params, timeout=self.config.timeout)
                response.raise_for_status()
                data = response.json()
                
                if self.enable_cache and cache_file:
                    self._cache_response(cache_file, data)
                
                return data
                
            except requests.exceptions.Timeout:
                logger.debug(f"Timeout for {url}, attempt {attempt+1}")
                if attempt == self.config.retry_attempts - 1:
                    return None
                time.sleep(1)
            except requests.exceptions.RequestException as e:
                logger.error(f"Request failed for {url}: {e}")
                return None
            except ValueError as e:
                logger.error(f"JSON decode failed for {url}: {e}")
                return None
        
        return None

    def _validate_weather_data(self, data: WeatherData) -> bool:
        required_fields = ['temperature', 'description', 'source', 'city']
        
        for field in required_fields:
            if field not in data or data[field] is None:
                logger.warning(f"Missing required field: {field}")
                return False
        
        try:
            float(data['temperature'])
            return True
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid temperature value: {e}")
            return False

    def get_open_meteo(self) -> Optional[WeatherData]:
        try:
            url = "https://api.open-meteo.com/v1/forecast"
            params = {
                'latitude': self.latitude,
                'longitude': self.longitude,
                'current': 'temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,pressure_msl,wind_speed_10m,wind_direction_10m',
                'timezone': 'Europe/Vilnius'
            }
            
            data = self._make_request(url, params)
            if not data or 'current' not in data:
                logger.warning("Open-Meteo API returned invalid data")
                return None
            
            current = data['current']
            temperature = current.get('temperature_2m')
            if temperature is None:
                logger.warning("Open-Meteo API missing temperature data")
                return None
            
            weather_code = current.get('weather_code')
            description = self.open_meteo_weather_codes.get(weather_code, "Unknown")
            
            weather_data: WeatherData = {
                'temperature': float(temperature),
                'feels_like': float(current.get('apparent_temperature', temperature)),
                'humidity': float(current.get('relative_humidity_2m', 0)),
                'pressure': float(current.get('pressure_msl', 0)),
                'wind_speed': float(current.get('wind_speed_10m', 0)),
                'wind_direction': float(current.get('wind_direction_10m', 0)),
                'description': description,
                'source': 'Open-Meteo',
                'city': self.city
            }
            
            if self._validate_weather_data(weather_data):
                return weather_data
            return None
            
        except (ValueError, TypeError) as e:
            logger.error(f"Open-Meteo data processing error: {e}")
            return None

    def get_weather_api(self) -> Optional[WeatherData]:
        try:
            url = "http://api.weatherapi.com/v1/current.json"
            params = {
                'key': self.weather_api_key,
                'q': self.city,
                'aqi': 'no'
            }
            
            data = self._make_request(url, params)
            if not data or 'current' not in data:
                logger.warning("WeatherAPI returned invalid data")
                return None
            
            current = data['current']
            temperature = current.get('temp_c')
            if temperature is None:
                logger.warning("WeatherAPI missing temperature data")
                return None
            
            weather_data: WeatherData = {
                'temperature': float(temperature),
                'feels_like': float(current.get('feelslike_c', temperature)),
                'humidity': float(current.get('humidity', 0)),
                'pressure': float(current.get('pressure_mb', 0)),
                'wind_speed': float(current.get('wind_kph', 0)) * KPH_TO_MPS,
                'wind_direction': float(current.get('wind_degree', 0)),
                'description': current.get('condition', {}).get('text', 'Unknown'),
                'source': 'WeatherAPI',
                'city': self.city
            }
            
            if self._validate_weather_data(weather_data):
                return weather_data
            return None
            
        except (ValueError, TypeError) as e:
            logger.error(f"WeatherAPI data processing error: {e}")
            return None

    def get_wttr_in(self) -> Optional[WeatherData]:
        try:
            encoded_city = quote(self.city)
            url = f"https://wttr.in/{encoded_city}"
            params = {'format': 'j1'}
            
            data = self._make_request(url, params)
            if not data or 'current_condition' not in data or not data['current_condition']:
                logger.warning("wttr.in API returned invalid data")
                return None
            
            current = data['current_condition'][0]
            temp_c = current.get('temp_C')
            if temp_c is None:
                logger.warning("wttr.in API missing temperature data")
                return None
            
            weather_data: WeatherData = {
                'temperature': float(temp_c),
                'feels_like': float(current.get('FeelsLikeC', temp_c)),
                'humidity': int(current.get('humidity', 0)),
                'pressure': int(current.get('pressure', 0)),
                'wind_speed': float(current.get('windspeedKmph', 0)) * KPH_TO_MPS,
                'wind_direction': int(current.get('winddirDegree', 0)),
                'description': current.get('weatherDesc', [{}])[0].get('value', 'Unknown'),
                'source': 'wttr.in',
                'city': self.city
            }
            
            if self._validate_weather_data(weather_data):
                return weather_data
            return None
            
        except (ValueError, TypeError) as e:
            logger.error(f"wttr.in data processing error: {e}")
            return None

    def get_all_weather_data(self) -> Dict[str, WeatherData]:
        results = {}
        failed_sources = []
        
        api_functions = [
            ('Open-Meteo', self.get_open_meteo),
            ('wttr.in', self.get_wttr_in),
            ('WeatherAPI', self.get_weather_api)
        ]
        
        for name, api_func in api_functions:
            try:
                result = api_func()
                if result:
                    results[name] = result
                    logger.info(f"Successfully fetched from {name}")
                else:
                    failed_sources.append(name)
                    logger.warning(f"Failed to fetch from {name}")
            except Exception as e:
                failed_sources.append(name)
                logger.error(f"Exception fetching from {name}: {e}")
            
            time.sleep(self.config.request_delay)
        
        if failed_sources:
            logger.info(f"Failed sources: {failed_sources}")
        
        return results

def format_weather_report(results: Dict[str, WeatherData]) -> str:
    if not results:
        return "No weather data could be retrieved from any source.\n"
    
    report = f"{results[next(iter(results))].get('city', 'WEATHER')} REPORT\n"
    report += "=" * 40 + "\n"
    report += f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    
    for source, data in results.items():
        report += f"{source}:\n"
        report += f"  Temperature: {data['temperature']:.1f}°C\n"
        
        feels_like = data.get('feels_like')
        if feels_like is not None:
            report += f"  Feels like: {feels_like:.1f}°C\n"
        
        report += f"  Conditions: {data['description']}\n"
        
        humidity = data.get('humidity')
        if humidity is not None:
            report += f"  Humidity: {humidity:.0f}%\n"
        
        pressure = data.get('pressure')
        if pressure is not None:
            report += f"  Pressure: {pressure:.0f} hPa\n"
        
        wind_speed = data.get('wind_speed')
        if wind_speed is not None:
            report += f"  Wind: {wind_speed:.1f} m/s\n"
        
        report += "\n"
    
    temps = [data['temperature'] for data in results.values() if data.get('temperature') is not None]
    if temps:
        avg_temp = sum(temps) / len(temps)
        report += f"Average Temperature: {avg_temp:.1f}°C\n"
    
    report += f"Successful sources: {len(results)}\n"
    
    return report

class WeatherAppGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Weather Dashboard")
        self.root.geometry("1000x750")
        
        self.bg_color = "#f0f8ff"
        self.card_bg = "#ffffff"
        self.accent_color = "#4a90e2"
        self.text_color = "#333333"
        
        self.root.configure(bg=self.bg_color)
        
        self.city_var = tk.StringVar(value="Vilnius")
        self.lat_var = tk.StringVar(value="54.6872")
        self.lon_var = tk.StringVar(value="25.2797")
        self.enable_cache_var = tk.BooleanVar(value=False)
        self.is_fetching = False
        
        self.setup_styles()
        self.create_widgets()
        
    def setup_styles(self):
        self.style = ttk.Style()
        
        self.style.configure(
            'Accent.TButton',
            background=self.accent_color,
            foreground='white',
            font=('Segoe UI', 10, 'bold'),
            padding=10
        )
        
        self.style.configure(
            'Card.TFrame',
            background=self.card_bg,
            relief='solid',
            borderwidth=1
        )
        
    def create_widgets(self):
        main_container = tk.Frame(self.root, bg=self.bg_color)
        main_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        title_frame = tk.Frame(main_container, bg=self.bg_color)
        title_frame.pack(fill=tk.X, pady=(0, 20))
        
        title_label = tk.Label(
            title_frame,
            text="Weather Dashboard Pro",
            font=('Segoe UI', 24, 'bold'),
            bg=self.bg_color,
            fg=self.text_color
        )
        title_label.pack()
        
        subtitle_label = tk.Label(
            title_frame,
            text="Real-time weather from multiple sources",
            font=('Segoe UI', 12),
            bg=self.bg_color,
            fg='#666666'
        )
        subtitle_label.pack()
        
        control_card = ttk.Frame(main_container, style='Card.TFrame', padding=20)
        control_card.pack(fill=tk.X, pady=(0, 20))
        
        tk.Label(
            control_card,
            text="Location Settings",
            font=('Segoe UI', 14, 'bold'),
            bg=self.card_bg,
            fg=self.text_color
        ).pack(anchor=tk.W, pady=(0, 15))
        
        input_grid = tk.Frame(control_card, bg=self.card_bg)
        input_grid.pack(fill=tk.X)
        
        tk.Label(
            input_grid,
            text="City:",
            font=('Segoe UI', 10),
            bg=self.card_bg,
            fg=self.text_color
        ).grid(row=0, column=0, sticky=tk.W, pady=5, padx=(0, 10))
        
        city_entry = ttk.Entry(
            input_grid,
            textvariable=self.city_var,
            font=('Segoe UI', 10),
            width=30
        )
        city_entry.grid(row=0, column=1, sticky=tk.W, pady=5)
        
        tk.Label(
            input_grid,
            text="Latitude:",
            font=('Segoe UI', 10),
            bg=self.card_bg,
            fg=self.text_color
        ).grid(row=1, column=0, sticky=tk.W, pady=5, padx=(0, 10))
        
        lat_entry = ttk.Entry(
            input_grid,
            textvariable=self.lat_var,
            font=('Segoe UI', 10),
            width=15
        )
        lat_entry.grid(row=1, column=1, sticky=tk.W, pady=5)
        
        tk.Label(
            input_grid,
            text="Longitude:",
            font=('Segoe UI', 10),
            bg=self.card_bg,
            fg=self.text_color
        ).grid(row=2, column=0, sticky=tk.W, pady=5, padx=(0, 10))
        
        lon_entry = ttk.Entry(
            input_grid,
            textvariable=self.lon_var,
            font=('Segoe UI', 10),
            width=15
        )
        lon_entry.grid(row=2, column=1, sticky=tk.W, pady=5)
        
        cache_check = ttk.Checkbutton(
            control_card,
            text="Enable API Response Caching",
            variable=self.enable_cache_var
        )
        cache_check.pack(anchor=tk.W, pady=(10, 0))
        
        self.fetch_btn = ttk.Button(
            control_card,
            text="Fetch Weather Data",
            command=self.fetch_weather,
            style='Accent.TButton'
        )
        self.fetch_btn.pack(pady=(20, 0))
        
        self.progress = ttk.Progressbar(
            control_card,
            mode='indeterminate',
            length=200
        )
        self.progress.pack(pady=(10, 0))
        
        display_container = tk.Frame(main_container, bg=self.bg_color)
        display_container.pack(fill=tk.BOTH, expand=True)
        
        self.notebook = ttk.Notebook(display_container)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        dashboard_frame = tk.Frame(self.notebook, bg=self.bg_color)
        self.notebook.add(dashboard_frame, text="Dashboard")
        
        self.weather_text = scrolledtext.ScrolledText(
            dashboard_frame,
            wrap=tk.WORD,
            font=('Consolas', 10),
            bg=self.card_bg,
            fg=self.text_color,
            relief='solid',
            borderwidth=1
        )
        self.weather_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        raw_frame = tk.Frame(self.notebook, bg=self.bg_color)
        self.notebook.add(raw_frame, text="Raw Data")
        
        self.raw_text = scrolledtext.ScrolledText(
            raw_frame,
            wrap=tk.WORD,
            font=('Consolas', 9),
            bg='#1e1e1e',
            fg='#ffffff',
            relief='solid',
            borderwidth=1
        )
        self.raw_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.status_var = tk.StringVar(value="Ready to fetch weather data")
        status_bar = tk.Label(
            main_container,
            textvariable=self.status_var,
            bg='#e8f4fc',
            fg='#2c5282',
            font=('Segoe UI', 9),
            relief='sunken',
            anchor=tk.W,
            padx=10
        )
        status_bar.pack(fill=tk.X, pady=(10, 0))
        
    def fetch_weather(self):
        if self.is_fetching:
            return
            
        city = self.city_var.get().strip()
        if not city:
            messagebox.showwarning("Input Error", "Please enter a city name.")
            return
            
        try:
            lat = float(self.lat_var.get())
            lon = float(self.lon_var.get())
        except ValueError:
            messagebox.showerror("Input Error", "Latitude and Longitude must be valid numbers.")
            return
        
        self.is_fetching = True
        self.fetch_btn.config(state='disabled')
        self.progress.start()
        self.status_var.set("Fetching weather data from APIs...")
        self.weather_text.delete(1.0, tk.END)
        self.raw_text.delete(1.0, tk.END)
        self.weather_text.insert(tk.END, "Please wait while we gather weather data...\n\n")
        
        thread = threading.Thread(
            target=self._fetch_weather_thread,
            args=(city, lat, lon),
            daemon=True
        )
        thread.start()
        
    def _fetch_weather_thread(self, city: str, lat: float, lon: float):
        try:
            weather_api = FreeWeatherAPI(
                city=city,
                lat=lat,
                lon=lon,
                enable_cache=self.enable_cache_var.get()
            )
            
            results = weather_api.get_all_weather_data()
            report = format_weather_report(results)
            
            self.root.after(0, self._update_display, results, report, city)
            
        except Exception as e:
            logger.error(f"Fetch thread error: {e}")
            self.root.after(0, self._handle_error, str(e))
        finally:
            self.root.after(0, self._fetch_complete)
    
    def _update_display(self, results: Dict[str, WeatherData], report: str, city: str):
        self.weather_text.delete(1.0, tk.END)
        
        if results:
            display_text = f"WEATHER FOR: {city}\n"
            display_text += "=" * 50 + "\n\n"
            
            source_colors = {
                'Open-Meteo': '#2ecc71',
                'WeatherAPI': '#3498db',
                'wttr.in': '#9b59b6'
            }
            
            for source, data in results.items():
                color = source_colors.get(source, '#333333')
                display_text += f"Source: {source}\n"
                display_text += f"  Temperature: {data['temperature']:.1f}°C\n"
                display_text += f"  Feels like: {data.get('feels_like', data['temperature']):.1f}°C\n"
                display_text += f"  Conditions: {data['description']}\n"
                display_text += f"  Humidity: {data.get('humidity', 0):.0f}%\n"
                display_text += f"  Pressure: {data.get('pressure', 0):.0f} hPa\n"
                display_text += f"  Wind: {data.get('wind_speed', 0):.1f} m/s\n"
                display_text += f"  Direction: {data.get('wind_direction', 0):.0f}°\n"
                display_text += "-" * 35 + "\n\n"
            
            temps = [data['temperature'] for data in results.values()]
            avg_temp = sum(temps) / len(temps)
            
            display_text += "\n" + "-" * 47 + "\n"
            display_text += f"Average Temperature: {avg_temp:.1f}°C\n"
            display_text += f"Sources: {len(results)} successful\n"
            display_text += f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            
            self.status_var.set(f"Successfully fetched data for {city} from {len(results)} sources")
        else:
            display_text = "No weather data could be retrieved\n"
            display_text += "\nPossible issues:\n"
            display_text += "  Check internet connection\n"
            display_text += "  Verify city name\n"
            display_text += "  APIs might be temporarily unavailable\n"
            
            self.status_var.set("Failed to fetch weather data")
        
        self.weather_text.insert(tk.END, display_text)
        
        self.raw_text.delete(1.0, tk.END)
        self.raw_text.insert(tk.END, report)
        
        self.notebook.select(0)
    
    def _handle_error(self, error_msg: str):
        self.weather_text.delete(1.0, tk.END)
        self.weather_text.insert(tk.END, f"Error fetching weather data:\n\n{error_msg}")
        self.status_var.set(f"Error: {error_msg}")
    
    def _fetch_complete(self):
        self.is_fetching = False
        self.fetch_btn.config(state='normal')
        self.progress.stop()

def main():
    root = tk.Tk()
    app = WeatherAppGUI(root)
    
    root.update_idletasks()
    width = root.winfo_width()
    height = root.winfo_height()
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 2) - (height // 2)
    root.geometry(f'{width}x{height}+{x}+{y}')
    
    root.mainloop()

if __name__ == "__main__":
    main()
