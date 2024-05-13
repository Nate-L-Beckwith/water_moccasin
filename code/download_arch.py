# download_arch.py
import os
import requests
import xml.etree.ElementTree as ET
import logging
from tqdm import tqdm, tqdm_notebook
from concurrent.futures import ThreadPoolExecutor, as_completed

# Set up logging
logging.basicConfig(filename='download_log.txt', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

def download_file(name, size, download_dir, progress_bars):
    result = {"filename": name, "removed_old": "No", "status": "Started"}
    if name:
        download_url = f"https://archive.org/download/efgamecubeusa/{name}"
        filename = os.path.join(download_dir, name.split('/')[-1])
        standard_size = 1400000000  # Standard size for .iso files (1.4 GB)
        
        if os.path.exists(filename):
            local_size = os.path.getsize(filename)
            if local_size == size:
                result["status"] = "Skipped"
                logging.info(f"Skipped {filename} (already exists, size matches).")
                return result
            elif filename.lower().endswith('.iso') and local_size != standard_size:
                os.remove(filename)
                result["removed_old"] = "Yes"
                logging.info(f"Removed {filename} due to size mismatch (expected: 1.4 GB).")

        try:
            with requests.get(download_url, stream=True) as r:
                r.raise_for_status()
                with open(filename, 'wb') as f, tqdm(total=size, unit='B', unit_scale=True, desc=filename, position=len(progress_bars)) as file_bar:
                    progress_bars.append(file_bar)
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            file_bar.update(len(chunk))
                            progress_bars[0].update(len(chunk))
            result["status"] = "Completed"
            logging.info(f"Downloaded {filename} - Size: {size} bytes.")
        except requests.RequestException as e:
            result["status"] = "Failed"
            logging.error(f"Failed to download {filename}. Error: {e}")
            print(f"\033[91mError: Failed to download {filename}. {e}\033[0m")
    return result

def download_files_from_xml(xml_url, download_dir):
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)
    response = requests.get(xml_url)
    root = ET.fromstring(response.content)
    files = [(file.get('name'), int(file.get('size', -1)), download_dir) for file in root.findall('.//file') if file.get('name')]
    total_size = sum(file[1] for file in files if file[1] > 0)

    overall_progress = tqdm(total=total_size, unit='B', unit_scale=True, desc="Overall Progress", position=0)
    progress_bars = [overall_progress]

    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(download_file, *file, progress_bars): file for file in files}
        for future in as_completed(futures):
            results.append(future.result())

    overall_progress.close()
    print("\033[92mAll files have been processed.\033[0m")
    for result in results:
        print(f"{result['filename']}: Removed Old Version - {result['removed_old']}, Status - {result['status']}")

# Specify the XML URL and the directory where you want to save the files
download_files_from_xml('https://dn720005.ca.archive.org/0/items/efgamecubeusa/efgamecubeusa_files.xml', r'\\DESKTOP-NATES\iso\Dolphin\Games\Pull_archive_zips')
