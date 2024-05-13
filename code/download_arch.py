# download_arch.py
import os
import requests
import xml.etree.ElementTree as ET
import logging
from datetime import datetime
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time

# Configure logging to capture detailed information about the download process
logging.basicConfig(filename='download_log.txt', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

def create_folder_for_iso(download_dir, iso_name):
    """ Create a directory for the ISO if it does not exist. """
    folder_name = os.path.splitext(iso_name)[0].replace('/', '_').replace('\\', '_')
    folder_path = os.path.join(download_dir, folder_name)
    os.makedirs(folder_path, exist_ok=True)
    return folder_path

def write_info_file(folder_path, info_content):
    """ Write or update an information file in the specified directory. """
    info_file_path = os.path.join(folder_path, 'info.txt')
    with open(info_file_path, 'w') as file:
        file.write(info_content)

def download_file(name, size, download_dir, progress_dict, verbose_event, standard_size=1459978240):
    """ Download a single file with progress tracking and verbose delay notification. """
    folder_path = create_folder_for_iso(download_dir, name)
    filename = os.path.join(folder_path, name)
    download_url = f"https://archive.org/download/efgamecubeusa/{name}"
    info_content = f"File: {name}\nSize: {size} bytes\nDownloaded on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"

    try:
        with requests.get(download_url, stream=True, timeout=20) as r:
            r.raise_for_status()
            progress = tqdm(total=size, unit='B', unit_scale=True, desc=name, position=progress_dict[name])
            start_time = time.time()
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    progress.update(len(chunk))
                # Check if verbose status update is needed
                if time.time() - start_time > 15:
                    verbose_event.set()  # Trigger the verbose status update
            progress.close()
        write_info_file(folder_path, info_content)
        return "Completed", False
    except requests.RequestException as e:
        logging.error(f"Failed to download {filename}. Error: {e}")
        return "Failed", False

def verbose_status(name, verbose_event):
    """ Provide verbose status updates if the download takes longer than expected. """
    verbose_event.wait()  # Wait for the event to be set
    print(f"Starting download of {name} is taking longer than expected...")

def download_files_from_xml(xml_url, download_dir):
    """ Download all files listed in an XML with concurrency and verbose status updates. """
    response = requests.get(xml_url)
    root = ET.fromstring(response.content)
    files = [(file.get('name'), int(file.get('size', -1)), download_dir) for file in root.findall('.//file') if file.get('name')]

    progress_dict = {name: idx for idx, (name, _, _) in enumerate(files)}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = []
        for file in files:
            verbose_event = threading.Event()
            # Start a verbose status updater thread
            threading.Thread(target=verbose_status, args=(file[0], verbose_event)).start()
            # Submit download task
            future = executor.submit(download_file, *file, progress_dict, verbose_event)
            futures.append(future)

        # Wait for all downloads to complete
        for future in as_completed(futures):
            future.result()

    logging.info("All files have been processed.")

# Specify the XML URL and the directory where you want to save the files
download_files_from_xml('https://dn720005.ca.archive.org/0/items/efgamecubeusa/efgamecubeusa_files.xml', r'M:\Dolphin\Games\Pull_archive_zips')
