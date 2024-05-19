# download_arch.py
import os
import shutil
import requests
import xml.etree.ElementTree as ET
import logging
from datetime import datetime
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configure logging
logging.basicConfig(filename='download_log.txt', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# def check_internet_speed():
#     """Check internet speed using speedtest-cli and return the download speed in Mbps."""
#     st = speedtest.Speedtest()
#     st.get_best_server()
#     download_speed = st.download() / 1_000_000  # Convert from bits/s to Mbps
#     return download_speed

def determine_concurrency(download_speed):
    """Determine the number of concurrent downloads based on the internet speed."""
    if download_speed > 50:
        return 4  # High speed, more concurrency
    else:
        return 2  # Lower speed, less concurrency

# Main functionalities
def create_folder_for_iso(download_dir, iso_name):
    folder_name = iso_name.split(' (')[0].replace('/', '_').replace('\\', '_')
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

def download_files_from_xml(xml_url, download_dir):
    speed = check_internet_speed()
    max_concurrent = determine_concurrency(speed)
    
    response = requests.get(xml_url)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    files = [(file.get('name'), int(file.get('size', -1)), download_dir) for file in root.findall('.//file') if file.get('name')]
    progress_dict = {name: idx for idx, (name, _, _) in enumerate(files)}
    
    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = {executor.submit(download_file, *f, progress_dict): f for f in files}
        for future in tqdm(as_completed(futures), total=len(files), desc="Overall Progress", unit="file"):
            future.result()

    logging.info("All files have been processed.")

# Example usage
if __name__ == "__main__":
    download_files_from_xml('https://dn720005.ca.archive.org/0/items/efgamecubeusa/efgamecubeusa_files.xml', "C:\\Users\\nate\\Documents\\test_dolph_loads\\")
