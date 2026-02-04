import cv2

def list_ports():
    is_working = True
    dev_port = 0
    working_ports = []
    while dev_port < 10:  # check first 10 ports
        camera = cv2.VideoCapture(dev_port)
        if not camera.isOpened():
            is_working = False
            print(f"Port {dev_port} is not working.")
        else:
            is_reading, img = camera.read()
            w = camera.get(3)
            h = camera.get(4)
            if is_reading:
                print(f"Port {dev_port} is working and reads images ({w}x{h})")
                working_ports.append(dev_port)
            else:
                print(f"Port {dev_port} is open but not reading data ({w}x{h})")
            camera.release()
        dev_port +=1
    return working_ports

if __name__ == "__main__":
    print("Listing available cameras...")
    list_ports()
