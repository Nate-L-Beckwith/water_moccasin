# file_manager.py
import xml.etree.ElementTree as ET
import requests
import logging

logging.basicConfig(level=logging.INFO)

def fetch_and_parse_xml(xml_url):
    logging.info(f"Fetching XML from {xml_url}")
    response = requests.get(xml_url)
    response.raise_for_status()
    logging.info("XML fetched successfully")
    return response.content

class FileManager:
    def __init__(self, xml_url=None, xml_file=None):
        if xml_url:
            self.xml_content = fetch_and_parse_xml(xml_url)
        elif xml_file:
            with open(xml_file, 'r') as file:
                self.xml_content = file.read()
        else:
            raise ValueError("Either xml_url or xml_file must be provided")

    def get_urls(self):
        logging.info("Parsing XML content for URLs")
        root = ET.fromstring(self.xml_content)
        urls = [elem.text for elem in root.findall('.//file/name')]
        logging.info(f"Found {len(urls)} URLs in the XML")
        return urls
