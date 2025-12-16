#!/usr/bin/env python3

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, font
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
            except OSError:
                pass

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
        except (IOError, TypeError):
            pass

    def _load_cached_response(self, cache_file: Path) -> Optional[Dict[str, Any]]:
        if not self.enable_cache:
            return None
            
        if not cache_file.exists():
            return None
            
        try:
            file_age = time.time() - cache_file.stat().st_mtime
            if file_age < self.config.cache_ttl:
                return json.loads(cache_file.read_text())
        except (IOError, json.JSONDecodeError):
            pass
            
        return None

    def _make_request(self, url: str, params: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        if not self._validate_url(url):
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
                if attempt == self.config.retry_attempts - 1:
                    return None
                time.sleep(1)
            except requests.exceptions.RequestException:
                return None
            except ValueError:
                return None
        
        return None

    def _validate_weather_data(self, data: WeatherData) -> bool:
        required_fields = ['temperature', 'description', 'source', 'city']
        
        for field in required_fields:
            if field not in data or data[field] is None:
                return False
        
        try:
            float(data['temperature'])
            return True
        except (ValueError, TypeError):
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
                return None
            
            current = data['current']
            temperature = current.get('temperature_2m')
            if temperature is None:
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
            
        except (ValueError, TypeError):
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
                return None
            
            current = data['current']
            temperature = current.get('temp_c')
            if temperature is None:
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
            
        except (ValueError, TypeError):
            return None

    def get_wttr_in(self) -> Optional[WeatherData]:
        try:
            encoded_city = quote(self.city)
            url = f"https://wttr.in/{encoded_city}"
            params = {'format': 'j1'}
            
            data = self._make_request(url, params)
            if not data or 'current_condition' not in data or not data['current_condition']:
                return None
            
            current = data['current_condition'][0]
            temp_c = current.get('temp_C')
            if temp_c is None:
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
            
        except (ValueError, TypeError):
            return None

    def get_all_weather_data(self) -> Dict[str, WeatherData]:
        results = {}
        
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
            except Exception:
                pass
            
            time.sleep(self.config.request_delay)
        
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
        self.root.geometry("1200x850")
        
        self.bg_color = "#0f172a"
        self.card_bg = "#1e293b"
        self.accent_color = "#3b82f6"
        self.secondary_color = "#10b981"
        self.text_color = "#f8fafc"
        self.muted_text = "#94a3b8"
        
        self.root.configure(bg=self.bg_color)
        
        self.city_var = tk.StringVar(value="Vilnius")
        self.lat_var = tk.StringVar(value="54.6872")
        self.lon_var = tk.StringVar(value="25.2797")
        self.enable_cache_var = tk.BooleanVar(value=False)
        self.is_fetching = False
        
        self.title_font = font.Font(family="Helvetica", size=28, weight="bold")
        self.subtitle_font = font.Font(family="Helvetica", size=12)
        self.heading_font = font.Font(family="Helvetica", size=16, weight="bold")
        self.body_font = font.Font(family="Helvetica", size=11)
        self.mono_font = font.Font(family="Consolas", size=10)
        
        self.setup_styles()
        self.create_widgets()
        
    def setup_styles(self):
        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        self.style.configure(
            'Title.TLabel',
            background=self.bg_color,
            foreground=self.text_color,
            font=self.title_font
        )
        
        self.style.configure(
            'Subtitle.TLabel',
            background=self.bg_color,
            foreground=self.muted_text,
            font=self.subtitle_font
        )
        
        self.style.configure(
            'Card.TFrame',
            background=self.card_bg,
            relief='flat',
            borderwidth=0
        )
        
        self.style.configure(
            'Card.TLabelframe',
            background=self.card_bg,
            foreground=self.text_color,
            relief='flat',
            borderwidth=0
        )
        
        self.style.configure(
            'Card.TLabelframe.Label',
            background=self.card_bg,
            foreground=self.text_color,
            font=self.heading_font
        )
        
        self.style.configure(
            'Primary.TButton',
            background=self.accent_color,
            foreground='white',
            font=self.body_font,
            borderwidth=0,
            focuscolor='none',
            padding=12
        )
        self.style.map('Primary.TButton',
            background=[('active', '#2563eb'), ('disabled', '#64748b')],
            foreground=[('disabled', '#cbd5e1')]
        )
        
        self.style.configure(
            'Secondary.TButton',
            background=self.card_bg,
            foreground=self.text_color,
            font=self.body_font,
            borderwidth=1,
            relief='solid',
            padding=8
        )
        
        self.style.configure(
            'Custom.TEntry',
            fieldbackground=self.card_bg,
            foreground=self.text_color,
            borderwidth=1,
            relief='solid',
            padding=8
        )
        
        self.style.configure(
            'Light.TLabel',
            background=self.card_bg,
            foreground=self.text_color,
            font=self.body_font
        )
        
        self.style.configure(
            'Muted.TLabel',
            background=self.card_bg,
            foreground=self.muted_text,
            font=self.body_font
        )
        
        self.style.configure(
            'Custom.TCheckbutton',
            background=self.card_bg,
            foreground=self.text_color
        )
        
        self.style.configure(
            'Custom.TNotebook',
            background=self.bg_color,
            borderwidth=0
        )
        self.style.configure(
            'Custom.TNotebook.Tab',
            background=self.card_bg,
            foreground=self.muted_text,
            padding=[15, 5],
            font=self.body_font
        )
        self.style.map('Custom.TNotebook.Tab',
            background=[('selected', self.accent_color), ('active', '#334155')],
            foreground=[('selected', 'white'), ('active', self.text_color)]
        )
        
    def create_widgets(self):
        main_container = tk.Frame(self.root, bg=self.bg_color)
        main_container.pack(fill=tk.BOTH, expand=True, padx=30, pady=30)
        
        header_frame = tk.Frame(main_container, bg=self.bg_color)
        header_frame.pack(fill=tk.X, pady=(0, 30))
        
        title_label = ttk.Label(
            header_frame,
            text="Weather Dashboard",
            style='Title.TLabel'
        )
        title_label.pack(anchor=tk.W)
        
        subtitle_label = ttk.Label(
            header_frame,
            text="Real-time weather data from multiple sources",
            style='Subtitle.TLabel'
        )
        subtitle_label.pack(anchor=tk.W)
        
        content_container = tk.Frame(main_container, bg=self.bg_color)
        content_container.pack(fill=tk.BOTH, expand=True)
        
        left_column = tk.Frame(content_container, bg=self.bg_color, width=350)
        left_column.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 20))
        left_column.pack_propagate(False)
        
        control_card = ttk.LabelFrame(
            left_column,
            text="Location Settings",
            style='Card.TLabelframe',
            padding=25
        )
        control_card.pack(fill=tk.BOTH, pady=(0, 20))
        
        city_frame = tk.Frame(control_card, bg=self.card_bg)
        city_frame.pack(fill=tk.X, pady=(0, 15))
        
        ttk.Label(
            city_frame,
            text="City Name",
            style='Muted.TLabel'
        ).pack(anchor=tk.W)
        
        city_entry = ttk.Entry(
            city_frame,
            textvariable=self.city_var,
            style='Custom.TEntry',
            font=self.body_font
        )
        city_entry.pack(fill=tk.X, pady=(5, 0))
        
        coord_frame = tk.Frame(control_card, bg=self.card_bg)
        coord_frame.pack(fill=tk.X, pady=(0, 20))
        
        ttk.Label(
            coord_frame,
            text="Coordinates",
            style='Muted.TLabel'
        ).pack(anchor=tk.W, pady=(0, 10))
        
        coord_grid = tk.Frame(coord_frame, bg=self.card_bg)
        coord_grid.pack(fill=tk.X)
        
        lat_container = tk.Frame(coord_grid, bg=self.card_bg)
        lat_container.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        
        ttk.Label(
            lat_container,
            text="Latitude",
            style='Muted.TLabel'
        ).pack(anchor=tk.W)
        
        lat_entry = ttk.Entry(
            lat_container,
            textvariable=self.lat_var,
            style='Custom.TEntry',
            font=self.body_font
        )
        lat_entry.pack(fill=tk.X, pady=(5, 0))
        
        lon_container = tk.Frame(coord_grid, bg=self.card_bg)
        lon_container.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        ttk.Label(
            lon_container,
            text="Longitude",
            style='Muted.TLabel'
        ).pack(anchor=tk.W)
        
        lon_entry = ttk.Entry(
            lon_container,
            textvariable=self.lon_var,
            style='Custom.TEntry',
            font=self.body_font
        )
        lon_entry.pack(fill=tk.X, pady=(5, 0))
        
        cache_frame = tk.Frame(control_card, bg=self.card_bg)
        cache_frame.pack(fill=tk.X, pady=(0, 25))
        
        cache_check = ttk.Checkbutton(
            cache_frame,
            text="Enable API Response Caching",
            variable=self.enable_cache_var,
            style='Custom.TCheckbutton'
        )
        cache_check.pack(anchor=tk.W)
        
        self.fetch_btn = ttk.Button(
            control_card,
            text="FETCH WEATHER DATA",
            command=self.fetch_weather,
            style='Primary.TButton',
            cursor='hand2'
        )
        self.fetch_btn.pack(fill=tk.X, pady=(0, 15))
        
        self.progress = ttk.Progressbar(
            control_card,
            mode='indeterminate',
            style='Custom.Horizontal.TProgressbar'
        )
        self.progress.pack(fill=tk.X)
        
        info_card = tk.Frame(left_column, bg=self.card_bg, padx=25, pady=25)
        info_card.pack(fill=tk.BOTH)
        
        ttk.Label(
            info_card,
            text="Weather Sources",
            style='Light.TLabel',
            font=self.heading_font
        ).pack(anchor=tk.W, pady=(0, 15))
        
        sources = [
            ("Open-Meteo", "Free weather API with global coverage"),
            ("WeatherAPI", "Commercial API with demo access"),
            ("wttr.in", "Popular terminal weather service")
        ]
        
        for source, description in sources:
            source_frame = tk.Frame(info_card, bg=self.card_bg)
            source_frame.pack(fill=tk.X, pady=(0, 10))
            
            ttk.Label(
                source_frame,
                text=source,
                style='Light.TLabel',
                font=('Helvetica', 11, 'bold')
            ).pack(anchor=tk.W)
            
            ttk.Label(
                source_frame,
                text=description,
                style='Muted.TLabel',
                font=('Helvetica', 9)
            ).pack(anchor=tk.W, pady=(2, 0))
        
        right_column = tk.Frame(content_container, bg=self.bg_color)
        right_column.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.notebook = ttk.Notebook(
            right_column,
            style='Custom.TNotebook'
        )
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        dashboard_frame = tk.Frame(self.notebook, bg=self.bg_color, padx=5, pady=5)
        self.notebook.add(dashboard_frame, text="Dashboard")
        
        self.weather_text = scrolledtext.ScrolledText(
            dashboard_frame,
            wrap=tk.WORD,
            font=self.mono_font,
            bg=self.card_bg,
            fg=self.text_color,
            relief='flat',
            borderwidth=0,
            padx=20,
            pady=20
        )
        self.weather_text.pack(fill=tk.BOTH, expand=True)
        
        self.weather_text.tag_configure("title", foreground=self.accent_color, font=('Consolas', 14, 'bold'))
        self.weather_text.tag_configure("source", foreground=self.secondary_color, font=('Consolas', 12, 'bold'))
        self.weather_text.tag_configure("label", foreground=self.muted_text, font=('Consolas', 10))
        self.weather_text.tag_configure("value", foreground=self.text_color, font=('Consolas', 10, 'bold'))
        self.weather_text.tag_configure("divider", foreground=self.muted_text)
        
        raw_frame = tk.Frame(self.notebook, bg=self.bg_color, padx=5, pady=5)
        self.notebook.add(raw_frame, text="Raw Data")
        
        self.raw_text = scrolledtext.ScrolledText(
            raw_frame,
            wrap=tk.WORD,
            font=self.mono_font,
            bg='#0d1117',
            fg='#c9d1d9',
            relief='flat',
            borderwidth=0,
            padx=20,
            pady=20
        )
        self.raw_text.pack(fill=tk.BOTH, expand=True)
        
        status_container = tk.Frame(right_column, bg=self.bg_color, height=40)
        status_container.pack(fill=tk.X, pady=(15, 0))
        status_container.pack_propagate(False)
        
        status_bar = tk.Frame(status_container, bg=self.card_bg)
        status_bar.pack(fill=tk.BOTH, expand=True)
        
        self.status_var = tk.StringVar(value="Ready to fetch weather data")
        status_label = ttk.Label(
            status_bar,
            textvariable=self.status_var,
            style='Muted.TLabel',
            padding=(15, 10)
        )
        status_label.pack(side=tk.LEFT)
        
        self.time_var = tk.StringVar()
        time_label = ttk.Label(
            status_bar,
            textvariable=self.time_var,
            style='Muted.TLabel',
            padding=(15, 10)
        )
        time_label.pack(side=tk.RIGHT)
        
        self.update_time()
        
    def update_time(self):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.time_var.set(current_time)
        self.root.after(1000, self.update_time)
        
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
            self.root.after(0, self._handle_error, str(e))
        finally:
            self.root.after(0, self._fetch_complete)
    
    def _update_display(self, results: Dict[str, WeatherData], report: str, city: str):
        self.weather_text.delete(1.0, tk.END)
        
        if results:
            self.weather_text.insert(tk.END, f"Weather for {city}\n", "title")
            self.weather_text.insert(tk.END, "─" * 50 + "\n\n", "divider")
            
            source_colors = {
                'Open-Meteo': self.secondary_color,
                'WeatherAPI': self.accent_color,
                'wttr.in': '#8b5cf6'
            }
            
            for source, data in results.items():
                color = source_colors.get(source, self.text_color)
                self.weather_text.tag_configure(f"source_{source}", foreground=color, font=('Consolas', 12, 'bold'))
                
                self.weather_text.insert(tk.END, f"{source}\n", f"source_{source}")
                
                self.weather_text.insert(tk.END, "Temperature: ", "label")
                self.weather_text.insert(tk.END, f"{data['temperature']:.1f}°C\n", "value")
                
                feels_like = data.get('feels_like', data['temperature'])
                self.weather_text.insert(tk.END, "Feels like: ", "label")
                self.weather_text.insert(tk.END, f"{feels_like:.1f}°C\n", "value")
                
                self.weather_text.insert(tk.END, "Conditions: ", "label")
                self.weather_text.insert(tk.END, f"{data['description']}\n", "value")
                
                humidity = data.get('humidity', 0)
                self.weather_text.insert(tk.END, "Humidity: ", "label")
                self.weather_text.insert(tk.END, f"{humidity:.0f}%\n", "value")
                
                pressure = data.get('pressure', 0)
                self.weather_text.insert(tk.END, "Pressure: ", "label")
                self.weather_text.insert(tk.END, f"{pressure:.0f} hPa\n", "value")
                
                wind_speed = data.get('wind_speed', 0)
                self.weather_text.insert(tk.END, "Wind Speed: ", "label")
                self.weather_text.insert(tk.END, f"{wind_speed:.1f} m/s\n", "value")
                
                wind_dir = data.get('wind_direction', 0)
                self.weather_text.insert(tk.END, "Wind Direction: ", "label")
                self.weather_text.insert(tk.END, f"{wind_dir:.0f}°\n", "value")
                
                self.weather_text.insert(tk.END, "\n" + "─" * 40 + "\n\n", "divider")
            
            temps = [data['temperature'] for data in results.values()]
            avg_temp = sum(temps) / len(temps)
            
            self.weather_text.insert(tk.END, "Summary\n", "title")
            self.weather_text.insert(tk.END, "Average Temperature: ", "label")
            self.weather_text.insert(tk.END, f"{avg_temp:.1f}°C\n", "value")
            self.weather_text.insert(tk.END, "Sources: ", "label")
            self.weather_text.insert(tk.END, f"{len(results)} successful\n", "value")
            self.weather_text.insert(tk.END, "Last updated: ", "label")
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.weather_text.insert(tk.END, f"{current_time}\n", "value")
            
            self.status_var.set(f"Successfully fetched data for {city} from {len(results)} sources")
        else:
            self.weather_text.insert(tk.END, "No weather data could be retrieved\n\n", "title")
            self.weather_text.insert(tk.END, "Possible issues:\n", "label")
            self.weather_text.insert(tk.END, "• Check internet connection\n", "value")
            self.weather_text.insert(tk.END, "• Verify city name\n", "value")
            self.weather_text.insert(tk.END, "• APIs might be temporarily unavailable\n", "value")
            
            self.status_var.set("Failed to fetch weather data")
        
        self.raw_text.delete(1.0, tk.END)
        self.raw_text.insert(tk.END, report)
        
        self.notebook.select(0)
    
    def _handle_error(self, error_msg: str):
        self.weather_text.delete(1.0, tk.END)
        self.weather_text.insert(tk.END, "Error fetching weather data:\n\n", "title")
        self.weather_text.insert(tk.END, error_msg, "value")
        self.status_var.set(f"Error: {error_msg}")
    
    def _fetch_complete(self):
        self.is_fetching = False
        self.fetch_btn.config(state='normal')
        self.progress.stop()

def main():
    root = tk.Tk()
    app = WeatherAppGUI(root)
    
    root.update_idletasks()
    width = 1200
    height = 850
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 2) - (height // 2)
    root.geometry(f'{width}x{height}+{x}+{y}')
    
    root.mainloop()

if __name__ == "__main__":
    main()
