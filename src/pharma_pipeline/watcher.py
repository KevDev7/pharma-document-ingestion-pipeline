import queue
import threading
import time
from pathlib import Path
from typing import Optional

from .pipeline import IngestionPipeline


def wait_for_stable_file(
    path: Path,
    poll_seconds: float = 0.5,
    stable_checks: int = 3,
    timeout_seconds: float = 30.0,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    last_size: Optional[int] = None
    unchanged = 0
    while time.monotonic() < deadline:
        if not path.exists():
            return False
        size = path.stat().st_size
        if size == last_size and size > 0:
            unchanged += 1
            if unchanged >= stable_checks:
                return True
        else:
            unchanged = 0
            last_size = size
        time.sleep(poll_seconds)
    return False


def watch_directory(pipeline: IngestionPipeline) -> None:
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError as error:
        raise RuntimeError("Install the watchdog package to use the watch command") from error

    pending: "queue.Queue[Path]" = queue.Queue()
    queued = set()
    lock = threading.Lock()

    class PdfEventHandler(FileSystemEventHandler):
        def _enqueue(self, raw_path: str) -> None:
            path = Path(raw_path).resolve()
            if path.suffix.lower() != ".pdf":
                return
            with lock:
                if path in queued:
                    return
                queued.add(path)
            pending.put(path)

        def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
            if not event.is_directory:
                self._enqueue(event.src_path)

        def on_moved(self, event) -> None:  # type: ignore[no-untyped-def]
            if not event.is_directory:
                self._enqueue(event.dest_path)

    initial = pipeline.scan_incoming()
    if initial["discovered_files"]:
        print(f"Initial scan: {initial['processed_files']} processed, {initial['skipped_files']} skipped")

    observer = Observer()
    observer.schedule(PdfEventHandler(), str(pipeline.settings.incoming_dir), recursive=False)
    observer.start()
    print(f"Watching {pipeline.settings.incoming_dir} for new PDFs. Press Ctrl+C to stop.")

    try:
        while True:
            try:
                path = pending.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                if wait_for_stable_file(path):
                    result = pipeline.ingest_paths(
                        [path], trigger_type="file_created_event", move_after_processing=True
                    )
                    item = result["results"][0]
                    print(f"{path.name}: {item['outcome']}")
                else:
                    print(f"{path.name}: file did not become stable before timeout")
            finally:
                with lock:
                    queued.discard(path)
                pending.task_done()
    except KeyboardInterrupt:
        print("Stopping watcher.")
    finally:
        observer.stop()
        observer.join()
