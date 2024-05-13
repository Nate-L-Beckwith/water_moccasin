import os
import requests
import xml.etree.ElementTree as ET
import logging
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor

# Set up logging
logging.basicConfig(filename='download_log.txt', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

def download_file(name, size, download_dir):
    if name:
        download_url = f"https://archive.org/download/efgamecubeusa/{name}"
        filename = os.path.join(download_dir, name.split('/')[-1])
        if os.path.exists(filename):
            local_size = os.path.getsize(filename)
            if local_size == size:
                logging.info(f"Skipped {filename} (already exists, size matches).")
                return
        try:
            with requests.get(download_url, stream=True) as r:
                r.raise_for_status()
                with open(filename, 'wb') as f, tqdm(total=size, unit='B', unit_scale=True, desc=filename) as bar:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            bar.update(len(chunk))
            logging.info(f"Downloaded {filename} - Size: {size} bytes.")
        except requests.RequestException as e:
            logging.error(f"Failed to download {filename}. Error: {e}")

def download_files_from_xml(xml_url, download_dir):
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)
    response = requests.get(xml_url)
    root = ET.fromstring(response.content)
    files = [(file.get('name'), int(file.get('size', -1)), download_dir) for file in root.findall('.//file') if file.get('name')]

    with ThreadPoolExecutor(max_workers=5) as executor:  # Adjust the number of workers based on your connection and capabilities
        executor.map(lambda f: download_file(*f), files)

# Specify the XML URL and the directory where you want to save the files
download_files_from_xml('https://dn720005.ca.archive.org/0/items/efgamecubeusa/efgamecubeusa_files.xml', r'\\DESKTOP-NATES\iso\Dolphin\Games\Pull_archive_zips')
