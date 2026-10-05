"""Current read status, separate from persisted alert history."""
import threading
from datetime import datetime,timezone


class ReadHealth:
    def __init__(self):
        self.lock=threading.Lock()
        self.reads={name:dict(status='Not checked',last_attempt=None,last_success=None,error=None,http=None,detail=None)
                    for name in ('account','scanner','stream')}

    def success(self,name,now=None,detail=None):
        now=now or datetime.now(timezone.utc)
        with self.lock:
            self.reads[name]=dict(status='OK',last_attempt=now.isoformat(),last_success=now.isoformat(),
                                  error=None,http=None,detail=detail)

    def failure(self,name,exc,now=None):
        now=now or datetime.now(timezone.utc)
        code=getattr(exc,'status_code',None)
        with self.lock:
            self.reads[name]=dict(self.reads[name],status='Unavailable',last_attempt=now.isoformat(),
                                  error=type(exc).__name__,http=code if type(code) is int and 100<=code<=599 else None,
                                  detail=None)

    def snapshot(self,now=None):
        now=now or datetime.now(timezone.utc)
        with self.lock:result={name:dict(row) for name,row in self.reads.items()}
        for name,row in result.items():
            at=datetime.fromisoformat(row['last_success']) if row['last_success'] else None
            age=(now-at).total_seconds() if at else None
            row['fresh']=row['status']=='OK' and age is not None and 0<=age<=dict(account=15,scanner=120,stream=15)[name]
            row['age_seconds']=age
        return result
