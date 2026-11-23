"""One bounded background calculation; no UI calls from the worker thread."""
from concurrent.futures import ThreadPoolExecutor
import threading


class CalculationQueue:
    def __init__(self):
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='laminar-calc')
        self.lock=threading.Lock();self.key=None;self.future=None

    def submit(self,key,function,*args):
        with self.lock:
            if self.key==key:return self.future
            if self.future is not None and not self.future.done():return None
            self.key=key;self.future=self.executor.submit(function,*args)
            return self.future
