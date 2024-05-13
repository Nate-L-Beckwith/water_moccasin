# download_arch.py
import os
import requests
import xml.etree.ElementTree as ET
import logging
from tqdm import tqdm

# Set up logging
logging.basicConfig(filename='download_log.txt', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

def download_files_from_xml(xml_url, download_dir):
    # Ensure the download directory exists
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)

    # Fetch and parse the XML file
    response = requests.get(xml_url)
    root = ET.fromstring(response.content)
    total_downloaded = 0

    # Find all <file> elements and extract the URL for download
    for file_element in root.findall('.//file'):
        name = file_element.get('name')
        size = int(file_element.get('size', -1))  # Get the 'size' attribute if it exists, otherwise -1
        if name:  # Checking if name attribute exists
            download_url = f"https://archive.org/download/efgamecubeusa/{name}"
            filename = os.path.join(download_dir, name.split('/')[-1])
            
            # Check if file exists and compare sizes
            if os.path.exists(filename):
                local_size = os.path.getsize(filename)
                if local_size == size:
                    logging.info(f"Skipped {filename} (already exists, size matches).")
                    continue

            try:
                print(f"Downloading {filename}...")
                with requests.get(download_url, stream=True) as r:
                    r.raise_for_status()  # Check for request errors
                    with open(filename, 'wb') as f, tqdm(total=size, unit='B', unit_scale=True, desc=filename) as bar:
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                                bar.update(len(chunk))
                logging.info(f"Downloaded {filename} - Size: {size} bytes.")
                total_downloaded += size
            except requests.RequestException as e:
                logging.error(f"Failed to download {filename}. Error: {e}")
    
    logging.info(f"Total downloaded: {total_downloaded} bytes")

# Specify the XML URL and the directory where you want to save the files
download_files_from_xml('https://dn720005.ca.archive.org/0/items/efgamecubeusa/efgamecubeusa_files.xml', r'\\DESKTOP-NATES\iso\Dolphin\Games\Pull_archive_zips')
