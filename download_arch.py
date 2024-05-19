import os
import requests
import xml.etree.ElementTree as ET
import logging
from datetime import datetime
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED

# Configure logging
logging.basicConfig(filename='download_log.txt', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Set the number of concurrent downloads
MAX_CONCURRENT_DOWNLOADS = 4

def sanitize_filename(name):
    return name.replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')

def create_folder_for_iso(download_dir, iso_name):
    folder_name = sanitize_filename(iso_name.split(' (')[0])
    folder_path = os.path.join(download_dir, folder_name)
    os.makedirs(folder_path, exist_ok=True)
    logging.info(f"Created folder: {folder_path}")
    print(f"Created folder: {folder_path}")
    return folder_path

def write_info_file(folder_path, name, size):
    info_file_path = os.path.join(folder_path, 'info.txt')
    info_content = f"File: {name}\nSize: {size} bytes\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    with open(info_file_path, 'w', encoding='utf-8') as file:
        file.write(info_content)
    logging.info(f"Created info file: {info_file_path}")

def download_file(name, size, download_dir, progress_dict):
    folder_path = create_folder_for_iso(download_dir, name)
    write_info_file(folder_path, name, size)
    sanitized_name = sanitize_filename(name)
    filename = os.path.join(folder_path, sanitized_name)
    download_url = f"https://archive.org/download/efgamecubeusa/{name}"
    try:
        with requests.get(download_url, stream=True, timeout=10) as r:
            r.raise_for_status()
            with open(filename, 'wb') as f, tqdm(total=size, unit='B', unit_scale=True, desc=name, position=progress_dict[name]) as progress:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        progress.update(len(chunk))
        final_folder_name = sanitized_name.split(' (')[0]
        final_folder_path = os.path.join(download_dir, final_folder_name)
        os.rename(folder_path, final_folder_path)
        logging.info(f"Renamed folder to: {final_folder_path}")
        print(f"Download completed and folder renamed to: {final_folder_path}")
        return "Completed"
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to download {filename}. Error: {e}")
        print(f"Failed to download {filename}. Error: {e}")
        return "Failed"
    except FileNotFoundError as e:
        logging.error(f"File not found: {filename}. Error: {e}")
        print(f"File not found: {filename}. Error: {e}")
        return "Failed"

def download_files_from_xml(xml_url, download_dir):
    logging.info(f"Fetching XML from {xml_url}")
    print(f"Fetching XML from {xml_url}")
    response = requests.get(xml_url, timeout=10)
    response.raise_for_status()
    logging.info("XML fetched successfully")

    root = ET.fromstring(response.content)

    logging.info("Inspecting XML structure")
    for child in root.iter():
        logging.info(f"Tag: {child.tag}, Attributes: {child.attrib}, Text: {child.text}")

    files = [(file.get('name'), int(file.get('size', -1)), download_dir) for file in root.findall('.//file') if file.get('name')]

    logging.info(f"Found {len(files)} files to download")
    print(f"Found {len(files)} files to download")

    if not files:
        logging.warning("No files found to download")
        print("No files found to download")
        return

    progress_dict = {name: idx for idx, (name, _, _) in enumerate(files)}

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOWNLOADS) as executor:
        futures = {}
        for name, size, download_dir in tqdm(files, desc="Submitting Downloads", unit="file"):
            while len(futures) >= MAX_CONCURRENT_DOWNLOADS:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    result = future.result()
                    if result == "Completed":
                        print(f"Download completed for: {futures[future]}")
                    else:
                        print(f"Download failed for: {futures[future]}")
                    del futures[future]
            
            future = executor.submit(download_file, name, size, download_dir, progress_dict)
            futures[future] = name

        for future in tqdm(as_completed(futures), total=len(futures), desc="Finishing Downloads", unit="file"):
            result = future.result()
            if result == "Completed":
                print(f"Download completed for: {futures[future]}")
            else:
                print(f"Download failed for: {futures[future]}")

    logging.info("All files have been processed.")
    print("All files have been processed.")

# Example usage
if __name__ == "__main__":
    download_files_from_xml('https://dn720005.ca.archive.org/0/items/efgamecubeusa/efgamecubeusa_files.xml', "C:\\Users\\nate\\Documents\\test_dolph_loads\\")
