from collections import deque


class Song:
    def __init__(self, title, url, webpage=None, duration=0, requester=None):
        self.title = title
        self.url = url
        self.webpage = webpage
        self.duration = duration
        self.requester = requester


class MusicPlayer:
    def __init__(self, guild_id):
        self.guild_id = guild_id

        self.queue = deque()

        self.current = None

        self.volume = 80

        self.bass = 0

        self.repeat = False

        self.shuffle = False

        self.autoplay = True

        self.history = []

        self.message = None

        self.voice = None

    def add(self, song: Song):
        self.queue.append(song)

    def next(self):
        if not self.queue:
            self.current = None
            return None

        self.current = self.queue.popleft()
        self.history.append(self.current)

        if len(self.history) > 50:
            self.history.pop(0)

        return self.current

    def clear(self):
        self.queue.clear()

    def skip(self):
        return self.next()

    def set_volume(self, volume):
        volume = max(0, min(volume, 200))
        self.volume = volume

    def set_bass(self, bass):
        bass = max(0, min(bass, 10))
        self.bass = bass


class PlayerManager:
    def __init__(self):
        self.players = {}

    def get(self, guild_id):
        if guild_id not in self.players:
            self.players[guild_id] = MusicPlayer(guild_id)

        return self.players[guild_id]

    def remove(self, guild_id):
        if guild_id in self.players:
            del self.players[guild_id]


player_manager = PlayerManager()
