import socket
import threading
import time
import webbrowser

from app import create_app

app = create_app()


def _open_browser_when_ready(port):
    address = ("127.0.0.1", port)
    for _ in range(50):
        try:
            with socket.create_connection(address, timeout=0.2):
                webbrowser.open(f"http://{address[0]}:{port}/")
                return
        except OSError:
            time.sleep(0.2)

    print(f"Cineverse is starting. Open http://{address[0]}:{port}/ in your browser.")


def _find_open_port():
    for port in range(5001, 5011):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
            try:
                server_socket.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("Ports 5001–5010 are busy. Close another Cineverse server and try again.")

if __name__=="__main__":
    port = _find_open_port()
    threading.Thread(target=_open_browser_when_ready, args=(port,), daemon=True).start()
    app.run(host="127.0.0.1", port=port, debug=False)
