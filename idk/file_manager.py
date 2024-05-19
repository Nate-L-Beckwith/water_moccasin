# file_manager.py
import xml.etree.ElementTree as ET

class FileManager:
    def __init__(self, xml_file):
        self.xml_file = xml_file

    def get_urls(self):
        tree = ET.parse(self.xml_file)
        root = tree.getroot()
        urls = [elem.text for elem in root.findall('.//url')]
        return urls
