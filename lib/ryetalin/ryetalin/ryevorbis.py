import mutagen
from .ryebase import RyeBase


class RyeVorbis(RyeBase):
    TRANSLATIONS = {
        'ALBUM': 'album',
        'TITLE': 'title',
        'ARTIST': 'artist',
        'ALBUMARTIST': 'albumartist',
        'TRACKNUMBER': 'tracknumber'
    }
    def __init__(self, filename):
        super().__init__(filename, mutagen.File(filename))
        self.load()
