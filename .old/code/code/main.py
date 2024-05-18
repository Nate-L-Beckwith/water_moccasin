# main.py
# Main driver of the application
from downloader import download_files_from_xml
from file_manager import delete_test_directory
from speed_test import check_internet_speed, determine_concurrency
from config import TEST_DIRECTORY, PRODUCTION_DIRECTORY, DEFAULT_CONCURRENCY

if __name__ == "__main__":
    # Determine internet speed and appropriate concurrency
    speed = check_internet_speed()
    max_concurrent = determine_concurrency(speed)  # This might override DEFAULT_CONCURRENCY

    # Select directory based on environment - switch comment as needed for environment
    download_dir = TEST_DIRECTORY  # For testing environment
    # download_dir = PRODUCTION_DIRECTORY  # For production environment

    # Optional: Uncomment to clean directory before downloading
    delete_test_directory(download_dir)

    # Start the download process
    download_files_from_xml('https://dn720005.ca.archive.org/0/items/efgamecubeusa/efgamecubeusa_files.xml', download_dir, max_concurrent)
