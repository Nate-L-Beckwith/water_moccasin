# main.py
from config import DEFAULT_CONCURRENCY, DEFAULT_DOWNLOAD_DIRECTORY
from downloader import download_files
from file_manager import FileManager

def main():
    xml_url = 'https://dn720005.ca.archive.org/0/items/efgamecubeusa/efgamecubeusa_files.xml'
    download_dir = DEFAULT_DOWNLOAD_DIRECTORY
    concurrency = DEFAULT_CONCURRENCY

    file_manager = FileManager(xml_url=xml_url)
    urls = file_manager.get_urls()  # Method to fetch URLs from XML

    download_files(urls, download_dir, concurrency)

if __name__ == "__main__":
    main()
