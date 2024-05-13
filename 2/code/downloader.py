# downloader.py
# Handles the downloading of files
import requests
from tqdm import tqdm
from .file_manager import create_folder_for_iso, write_info_file
from .logger import logging
from datetime import datetime
import os

def download_file(name, size, download_dir, progress_dict):
    folder_path = create_folder_for_iso(download_dir, name)
    filename = os.path.join(folder_path, name)
    download_url = f"https://archive.org/download/efgamecubeusa/{name}"
    try:
        with requests.get(download_url, stream=True) as r:
            r.raise_for_status()
            with open(filename, 'wb') as f, tqdm(total=size, unit='B', unit_scale=True, desc=name, position=progress_dict[name]) as progress:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        progress.update(len(chunk))
        write_info_file(folder_path, f"Downloaded {name} on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return "Completed"
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to download {filename}. Error: {e}")
        return "Failed"
