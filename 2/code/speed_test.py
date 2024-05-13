# speed_test.py
# Performs internet speed tests and determines concurrency levels
import speedtest
from .config import *

def check_internet_speed():
    st = speedtest.Speedtest()
    st.get_best_server()
    download_speed = st.download() / 1_000_000  # Convert from bits/s to Mbps
    return download_speed

def determine_concurrency(download_speed):
    if download_speed > 50:
        return 4
    else:
        return 2
