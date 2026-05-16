import datetime

class UnixDateTime:
    @staticmethod
    def get_unix_timestamp(dt):
        return int((dt - datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)).total_seconds())
