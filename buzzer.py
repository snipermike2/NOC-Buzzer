from pyModbusTCP.server import ModbusServer, DataBank, DataHandler
from time import sleep

server = ModbusServer(host='192.168.69.43', port=502, no_block=True, )
def srt():
    server.start()
def stp():
    server.stop()
def srver_set():
    server.data_bank.set_holding_registers(1, [1,10, 3, 10, 10])
def srver_unset():
   server.data_bank.set_holding_registers(1, [1, 0, 3, 10, 0])
def buzzer_on(pred_1):
    '''server = ModbusServer(host='192.168.69.43', port=502, no_block=True, )
    print("buzzer called")
    def srt():
        server.start()

    def stp():
        server.stop()

    def srver_set():
        server.data_bank.set_holding_registers(1, [1, 10, 3, 10, 10])

    def srver_unset():
        server.data_bank.set_holding_registers(1, [1, 0, 3, 10, 0])'''

    if pred_1 == 'Alarm':
        print("buzzer called")
        srver_set()
        srt()
        sleep(10)
        srver_unset()
        sleep(10)


#server = ModbusServer(host='192.168.69.43', port=502, no_block=True,)