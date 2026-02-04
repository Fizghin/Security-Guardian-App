import cv2

def list_ports():
    """
    Test the ports and returns a tuple with the available ports and the ones that are working.
    """
    non_working_ports = []
    working_ports = []
    available_ports = []
    
    # Check a reasonable range of indices (e.g., 0 to 5)
    for dev_port in range(6):
        try:
            camera = cv2.VideoCapture(dev_port)
            if not camera.isOpened():
                non_working_ports.append(dev_port)
                print(f"Port {dev_port} is not working (failed to open).")
            else:
                is_reading, img = camera.read()
                if is_reading:
                    print(f"Port {dev_port} is working and reading frames. Resolution: {img.shape[1]}x{img.shape[0]}")
                    working_ports.append(dev_port)
                else:
                    print(f"Port {dev_port} is open but failed to read frame.")
                    available_ports.append(dev_port)
                camera.release()
        except Exception as e:
            print(f"Port {dev_port} exception: {e}")
            non_working_ports.append(dev_port)
            
    return working_ports, available_ports, non_working_ports

if __name__ == '__main__':
    print("Scanning for camera ports...")
    working, available, non_working = list_ports()
