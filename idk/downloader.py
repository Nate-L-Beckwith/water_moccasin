# downloader.py
import os
import requests
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)

def create_folder_for_iso(download_dir, iso_name):
    folder_name = iso_name.split(' (')[0].replace('/', '_').replace('\\', '_')
    folder_path = os.path.join(download_dir, folder_name)
    os.makedirs(folder_path, exist_ok=True)
    return folder_path

def write_info_file(folder_path, info_content):
    info_file_path = os.path.join(folder_path, 'info.txt')
    with open(info_file_path, 'w') as file:
        file.write(info_content)

def download_file(name, download_dir, progress_dict):
    folder_path = create_folder_for_iso(download_dir, name)
    filename = os.path.join(folder_path, name)
    download_url = f"https://archive.org/download/efgamecubeusa/{name}"
    try:
        with requests.get(download_url, stream=True) as r:
            r.raise_for_status()
            size = int(r.headers.get('content-length', 0))
            with open(filename, 'wb') as f, tqdm(total=size, unit='B', unit_scale=True, desc=name, position=progress_dict[name]) as progress:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        progress.update(len(chunk))
        write_info_file(folder_path, f"Downloaded {name} on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"Successfully downloaded {name}")
        return "Completed"
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to download {filename}. Error: {e}")
        return "Failed"

def download_files(urls, download_dir, concurrency):
    progress_dict = {url.split('/')[-1]: i for i, url in enumerate(urls)}
    logging.info(f"Starting download of {len(urls)} files with concurrency {concurrency}")
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(tqdm(executor.map(lambda url: download_file(url.split('/')[-1], download_dir, progress_dict), urls), total=len(urls)))
    logging.info("Download process completed.")
    return results
