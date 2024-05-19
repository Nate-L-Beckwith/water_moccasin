# downloader.py
import os
import requests
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm

class Downloader:
    def __init__(self, concurrency):
        self.concurrency = concurrency

    def download_file(self, url, dest_folder):
        local_filename = os.path.join(dest_folder, url.split('/')[-1])
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(local_filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return local_filename

    def download_files(self, urls, dest_folder):
        if not os.path.exists(dest_folder):
            os.makedirs(dest_folder)

        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            list(tqdm(executor.map(lambda url: self.download_file(url, dest_folder), urls), total=len(urls)))
