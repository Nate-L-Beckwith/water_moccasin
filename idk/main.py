# main.py
from config import DOWNLOAD_CONCURRENCY
from downloader import Downloader
from file_manager import FileManager

def main():
    file_manager = FileManager('file_list.xml')  # Assuming file_list.xml contains the URLs to be downloaded
    urls = file_manager.get_urls()  # Method to fetch URLs from XML
    dest_folder = 'downloads'

    downloader = Downloader(DOWNLOAD_CONCURRENCY)
    downloader.download_files(urls, dest_folder)

if __name__ == "__main__":
    main()
