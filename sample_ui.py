import tkinter as tk
import threading
import requests
import time

# Set up the location for the weather information
CITY = 'New York'
WEATHER_URL = f"http://wttr.in/{CITY}?format=%C+%t"

# Global variables to control weather fetching
fetch_weather = False
weather_data = ""

# Function to get weather data
def get_weather():
    global weather_data, fetch_weather
    while fetch_weather:
        try:
            response = requests.get(WEATHER_URL)
            weather_data = response.text.strip()
            update_weather_display()
        except Exception as e:
            weather_data = f"Error: {str(e)}"
        
        time.sleep(10)  # Fetch weather data every 10 seconds

# Function to update the GUI with the latest weather data
def update_weather_display():
    weather_label.config(text=weather_data)

# Function to start weather fetching
def start_fetching():
    global fetch_weather
    if not fetch_weather:
        fetch_weather = True
        threading.Thread(target=get_weather, daemon=True).start()

# Function to stop weather fetching
def stop_fetching():
    global fetch_weather
    fetch_weather = False

# Set up the GUI
root = tk.Tk()
root.title("Weather Display")

weather_label = tk.Label(root, text="Weather information will be displayed here", font=('Arial', 16), padx=20, pady=20)
weather_label.pack()

start_button = tk.Button(root, text="Start", command=start_fetching, font=('Arial', 14))
start_button.pack(pady=10)

stop_button = tk.Button(root, text="Stop", command=stop_fetching, font=('Arial', 14))
stop_button.pack(pady=10)

# Start the GUI event loop
root.mainloop()
