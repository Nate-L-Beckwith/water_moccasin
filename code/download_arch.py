# download_arch.py
import os
import requests
import xml.etree.ElementTree as ET
import logging
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor

# Set up logging
logging.basicConfig(filename='download_log.txt', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

def download_file(name, size, download_dir, progress):
    if name:
        download_url = f"https://archive.org/download/efgamecubeusa/{name}"
        filename = os.path.join(download_dir, name.split('/')[-1])
        standard_size = 1400000000  # Standard size for .iso files (1.4 GB)

        if os.path.exists(filename):
            local_size = os.path.getsize(filename)
            if local_size == size:
                logging.info(f"Skipped {filename} (already exists, size matches).")
                return
            elif filename.lower().endswith('.iso') and local_size != standard_size:
                os.remove(filename)
                logging.info(f"Removed {filename} due to size mismatch (expected: 1.4 GB).")

        try:
            with requests.get(download_url, stream=True) as r:
                r.raise_for_status()
                with open(filename, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            progress.update(len(chunk))
            logging.info(f"Downloaded {filename} - Size: {size} bytes.")
        except requests.RequestException as e:
            logging.error(f"Failed to download {filename}. Error: {e}")
            print(f"\033[91mError: Failed to download {filename}. {e}\033[0m")

def download_files_from_xml(xml_url, download_dir):
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)
    response = requests.get(xml_url)
    root = ET.fromstring(response.content)
    files = [(file.get('name'), int(file.get('size', -1)), download_dir) for file in root.findall('.//file') if file.get('name')]
    total_size = sum(file[1] for file in files if file[1] > 0)

    print("\033[96mStarting download of files...\033[0m")
    with tqdm(total=total_size, unit='B', unit_scale=True, desc="Overall Progress") as progress:
        with ThreadPoolExecutor(max_workers=5) as executor:
            executor.map(lambda f: download_file(*f, progress), files)
    print("\033[92mAll files have been processed.\033[0m")

# Specify the XML URL and the directory where you want to save the files
download_files_from_xml('https://dn720005.ca.archive.org/0/items/efgamecubeusa/efgamecubeusa_files.xml', r'\\DESKTOP-NATES\iso\Dolphin\Games\Pull_archive_zips')
