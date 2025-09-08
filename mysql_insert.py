from __future__ import print_function
import mysql.connector
from datetime import datetime
import prediction_fin

#Current date and time
def date_time():
  now = datetime.now()
  current_date = now.strftime("%d/%m/%Y %H:%M:%S")
  return current_date

#Connection to Mysql
cnx = mysql.connector.connect(user='root', password='deTen.12345',
                              host= '127.0.0.1',
                              database = 'nocbuzzer')

cursor = cnx.cursor()

add_fault = ("INSERT INTO Faults "
             "(fault_description, fault_category, fault_date_time)"
             "VALUES (%(fault_description)s, %(fault_category)s, %(fault_date_time )s)")

#Notificationvariables

#fault_desc = prediction_fin.procsd_text #import from prediction_fin
#fault_cat = prediction_fin.most_common(prediction_fin.thislistt)

##Fault information
def insert_mysql(fault_desc, fault_cat):
    cnx = mysql.connector.connect(user='root', password='deTen.12345',
                                  host='127.0.0.1',
                                  database='nocbuzzer')
    cursor = cnx.cursor()
    add_fault = ("INSERT INTO Faults "
                 "(fault_description, fault_category, fault_date_time)"
                 "VALUES (%(fault_description)s, %(fault_category)s, %(fault_date_time )s)")

    fault_info = {

        'fault_description': fault_desc,
        'fault_category': fault_cat,
        'fault_date_time ': date_time(),
    }
    cursor.execute(add_fault, fault_info)
    cnx.commit()





cursor.close()
cnx.close()