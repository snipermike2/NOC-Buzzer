import numpy as np
import nltk
import classifier_fin
from statistics import mode
from scipy import stats as st
import time

start = time.time()


#Prediction list
ims_category = []

#Function to find the mode of the prediction list
def most_common(thislistt):
    fault_category = mode(thislistt)
    return fault_category


#Text preprocessing function
def preprocess_text(text):
    # Convert text to lowercase
    text = text.lower()

    # Remove punctuation and digits
    text = ''.join(c for c in text if not c.isdigit() and c.isalpha() or c.isspace())

    # Remove stop words and apply stemming
    tokens = nltk.word_tokenize(text)

    # Join tokens back into a string
    return ' '.join(tokens)



def classifier_pred(new_text):
    # New notification and is preprocessed
    #new_text = input("Enter: ")
    procsd_text = preprocess_text(new_text)

    # Transform processed text
    test_text = classifier_fin.cv.transform([procsd_text])

    # Logistic Regression Prediction
    pred_LR = classifier_fin.lr.predict(test_text)
    #print("Logistic regression prediction: ", pred_LR)
    # ims_category.append(pred_LR)

    # SVM prediction
    SVM_pred = classifier_fin.SVM.predict(test_text)
    #print("Support Vector Machine prediction: ", SVM_pred)
    # ims_category.append(SVM_pred)

    # Random Forest Prediction
    RF_pred = classifier_fin.RFC.predict(test_text)
    #print("Random forest prediction: ", RF_pred)
    # ims_category.append(RF_pred)

    # Naive Bayes Prediction
    Naive_pred = classifier_fin.Naive.predict(test_text)
    #print("Naive Bayes prediction: ", Naive_pred)
    # ims_category.append(Naive_pred)

    # Decision Tree prediction
    DTC_pred = classifier_fin.DTC.predict(test_text)
    #print("Decision Tree prediction: ", DTC_pred)
    # ims_category.append(DTC_pred)

    #thislistt = [''.join(SVM_pred.tolist()), ''.join(DTC_pred.tolist()), ''.join(pred_LR.tolist()),''.join(Naive_pred.tolist()), ''.join(RF_pred.tolist())]

    thislistt = [''.join(SVM_pred.tolist()), ''.join(DTC_pred.tolist()), ''.join(RF_pred.tolist())]

    print(thislistt)
    # print(ims_category)
    #print(most_common(thislistt))

    #print("This notification is an: ", most_common(thislistt))
    return most_common(thislistt)



end = time.time()
print(end - start)