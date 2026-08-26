import sys, time
if __name__=="__main__":
    x = int(sys.argv[1])
    time.sleep(5)
    if x<3:
        print(3)
    else:
        print(6)
