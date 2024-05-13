import os
import requests
import xml.etree.ElementTree as ET
import logging
from datetime import datetime
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor

# Configure logging
logging.basicConfig(filename='download_log.txt', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def create_folder_for_iso(download_dir, iso_name):
    """Create a directory based on the ISO name, ensuring proper sanitization of the folder name."""
    folder_name = iso_name.split(' (')[0]  # Assumes format "GameName (Region).iso"
    folder_name = folder_name.replace('/', '_').replace('\\', '_').replace(':', '_').replace('?', '_').replace('"', '_')
    folder_path = os.path.join(download_dir, folder_name)
    os.makedirs(folder_path, exist_ok=True)
    return folder_path

def write_info_file(folder_path, info_content):
    info_file_path = os.path.join(folder_path, 'info.txt')
    with open(info_file_path, 'w') as file:
        file.write(info_content)

def download_file(name, size, download_dir, progress_dict):
    folder_path = create_folder_for_iso(download_dir, name)
    filename = os.path.join(folder_path, name)
    download_url = f"https://archive.org/download/efgamecubeusa/{name}"
    info_content = f"File: {name}\nSize: {size} bytes\nDownloaded on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    
    try:
        with requests.get(download_url, stream=True) as r:
            r.raise_for_status()
            with open(filename, 'wb') as f, tqdm(total=size, unit='B', unit_scale=True, desc=name, position=progress_dict[name]) as progress:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        progress.update(len(chunk))
        write_info_file(folder_path, info_content)
        return "Completed", False
    except requests.RequestException as e:
        logging.error(f"Failed to download {filename}. Error: {e}")
        return "Failed", False

def download_files_from_xml(xml_url, download_dir):
    response = requests.get(xml_url)
    root = ET.fromstring(response.content)
    files = [(file.get('name'), int(file.get('size', -1)), download_dir) for file in root.findall('.//file') if file.get('name')]
    progress_dict = {name: idx for idx, (name, _, _) in enumerate(files)}
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(download_file, *f, progress_dict): f for f in files}
        for future in tqdm(as_completed(futures), total=len(files), desc="Overall Progress", unit="file"):
            future.result()

    logging.info("All files have been processed.")

# Specify the XML URL and the directory where you want to save the files
download_files_from_xml('https://dn720005.ca.archive.org/0/items/efgamecubeusa/efgamecubeusa_files.xml', r'M:\Dolphin\Games\Pull_archive_zips')
