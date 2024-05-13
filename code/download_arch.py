# download_arch.py
import os
import shutil
import requests
import xml.etree.ElementTree as ET
import logging
from datetime import datetime
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor

# Configure logging
logging.basicConfig(filename='download_log.txt', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Directory Configuration
runDir = "C:\\Users\\nate\\Documents\\test_dolph_loads\\"  # Switch as needed
download_dir = runDir

def delete_test_directory(path=download_dir):
    """Deletes the specified directory for testing purposes."""
    try:
        shutil.rmtree(path)
        print(f"Test directory '{path}' has been deleted for clean start.")
    except FileNotFoundError:
        print(f"Test directory '{path}' not found. No need to delete.")
    except Exception as e:
        print(f"Error occurred while deleting test directory: {e}")

def create_folder_for_iso(download_dir, iso_name):
    """Creates and returns a directory path for the given ISO."""
    folder_name = iso_name.split(' (')[0].replace('/', '_').replace('\\', '_')
    folder_path = os.path.join(download_dir, folder_name)
    os.makedirs(folder_path, exist_ok=True)
    print(f"Folder created: {folder_path}")  # Verbose output
    return folder_path

def rename_folder(post_download_folder_path):
    """Renames folder by removing a predefined prefix post-download."""
    base_path, folder_name = os.path.split(post_download_folder_path)
    new_name = folder_name.split('_', 1)[-1] if '_' in folder_name else folder_name
    new_path = os.path.join(base_path, new_name)
    os.rename(post_download_folder_path, new_path)
    print(f"Folder renamed from {post_download_folder_path} to {new_path}")  # Verbose output

def write_info_file(folder_path, info_content):
    """Writes information to a file in the specified directory."""
    info_file_path = os.path.join(folder_path, 'info.txt')
    with open(info_file_path, 'w') as file:
        file.write(info_content)
    print(f"Info file updated: {info_file_path}")  # Verbose output

def download_file(name, size, download_dir, progress_dict):
    """Downloads a file with detailed progress and logs."""
    folder_path = create_folder_for_iso(download_dir, name)
    filename = os.path.join(folder_path, name)
    download_url = f"https://archive.org/download/efgamecubeusa/{name}"
    try:
        with requests.get(download_url, stream=True, verify=True) as r:  # Improved SSL handling
            r.raise_for_status()
            with open(filename, 'wb') as f, tqdm(total=size, unit='B', unit_scale=True, desc=name, position=progress_dict[name]) as progress:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        progress.update(len(chunk))
        write_info_file(folder_path, f"Downloaded {name} on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        rename_folder(folder_path)  # Rename folder after download
        return "Completed"
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to download {filename}. Error: {e}")
        return "Failed"

def download_files_from_xml(xml_url, download_dir):
    """Manage file downloads from an XML list with concurrency."""
    try:
        response = requests.get(xml_url)
        root = ET.fromstring(response.content)
        files = [(file.get('name'), int(file.get('size', -1)), download_dir) for file in root.findall('.//file') if file.get('name')]
        progress_dict = {name: idx for idx, (name, _, _) in enumerate(files)}
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(download_file, *f, progress_dict): f for f in files}
            for future in tqdm(as_completed(futures), total=len(files), desc="Overall Progress", unit="file"):
                future.result()

        logging.info("All files have been processed.")
    except Exception as e:
        logging.error(f"An error occurred: {e}")

# Main execution block
if __name__ == "__main__":
    # Uncomment the next line to enable directory clean-up for testing
    # delete_test_directory()
    download_files_from_xml('https://dn720005.ca.archive.org/0/items/efgamecubeusa/efgamecubeusa_files.xml', download_dir)
