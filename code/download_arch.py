# download_arch.py
import os
import requests
import xml.etree.ElementTree as ET
import logging
from datetime import datetime
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor

# Configure logging for detailed admin usage
logging.basicConfig(filename='download_log.txt', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

def create_folder_for_iso(download_dir, iso_name):
    """ Create a directory for the ISO, providing verbose output for user and detailed logging. """
    folder_name = os.path.splitext(iso_name)[0].replace('/', '_').replace('\\', '_')
    folder_path = os.path.join(download_dir, folder_name)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"Created directory: {folder_path}")
        logging.info(f"Directory created: {folder_path}")
    else:
        print(f"Directory already exists: {folder_path}")
        logging.info(f"Directory exists: {folder_path}")
    return folder_path

def write_info_file(folder_path, info_content):
    """ Write information to a file with verbose output and logging. """
    info_file_path = os.path.join(folder_path, 'info.txt')
    with open(info_file_path, 'w') as file:
        file.write(info_content)
    print(f"Info file written for {folder_path}")
    logging.info(f"Info file updated for {folder_path}")

def download_file(name, size, download_dir, progress_dict):
    """ Download a file with detailed progress and logging. """
    folder_path = create_folder_for_iso(download_dir, name)
    filename = os.path.join(folder_path, name)
    download_url = f"https://archive.org/download/efgamecubeusa/{name}"
    info_content = f"File: {name}\nSize: {size} bytes\nDownloaded on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    
    try:
        response = requests.get(download_url, stream=True, timeout=10)
        response.raise_for_status()
        print(f"Connected to {download_url}")
        logging.info(f"Connection established to {download_url}")

        with open(filename, 'wb') as f, tqdm(total=size, unit='B', unit_scale=True, desc=name, position=progress_dict[name]) as progress:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    progress.update(len(chunk))
        write_info_file(folder_path, info_content)
        print(f"Download completed: {filename}")
        logging.info(f"Download successful: {filename}")
        return "Completed", False
    except requests.RequestException as e:
        print(f"Failed to connect or download {download_url}: {e}")
        logging.error(f"Connection or download failed for {filename}: {e}")
        return "Failed", False

def download_files_from_xml(xml_url, download_dir):
    """ Manage file downloads from an XML list with concurrency, verbose output, and detailed logging. """
    response = requests.get(xml_url)
    root = ET.fromstring(response.content)
    files = [(file.get('name'), int(file.get('size', -1)), download_dir) for file in root.findall('.//file') if file.get('name')]
    progress_dict = {name: idx for idx, (name, _, _) in enumerate(files)}

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(download_file, *f, progress_dict): f for f in files}
        for future in tqdm(as_completed(futures), total=len(files), desc="Overall Progress", unit="file"):
            future.result()

    print("All files have been processed.")
    logging.info("All files have been processed.")

# Specify the XML URL and the directory where you want to save the files
download_files_from_xml('https://dn720005.ca.archive.org/0/items/efgamecubeusa/efgamecubeusa_files.xml', r'M:\Dolphin\Games\Pull_archive_zips')
