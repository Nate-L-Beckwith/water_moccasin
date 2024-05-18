# file_manager.py
# Manages file and directory operations
import os

def create_folder_for_iso(download_dir, iso_name):
    folder_name = iso_name.split(' (')[0].replace('/', '_').replace('\\', '_')
    folder_path = os.path.join(download_dir, folder_name)
    os.makedirs(folder_path, exist_ok=True)
    return folder_path

def write_info_file(folder_path, info_content):
    info_file_path = os.path.join(folder_path, 'info.txt')
    with open(info_file_path, 'w') as file:
        file.write(info_content)
