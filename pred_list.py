import statistics
from statistics import mode

ims_category = []

ims_category.append("[Alarm]")
ims_category.append("[Warning]")
ims_category.append("[Informational]")
ims_category.append("[Warning]")
ims_category.append("[Warning]")

print(ims_category)

def most_common(ims_category):
    fault_category = mode(ims_category)
    print(fault_category)



most_common(ims_category)
