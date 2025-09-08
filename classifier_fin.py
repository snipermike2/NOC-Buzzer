import pandas as pd
import nltk
import re
from nltk.corpus import stopwords
from nltk.stem import WordNetLemmatizer
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn import model_selection, svm
from sklearn.ensemble import RandomForestClassifier
from sklearn import tree
from sklearn import model_selection, naive_bayes
#nltk.download('all')

#Reading Data
data = pd.read_csv('Dataset1.csv',  encoding= 'unicode_escape')


#Preprocessing loop
text = list(data['email_text'])
lemmatizer = WordNetLemmatizer()

corpus = []

for i in range(len(text)):
    r = re.sub('[^a-zA-Z]', ' ',text[i])
    r = r.lower()
    r = r.split()
    #r = [word for word in r if word not in stopwords.words('english')]
    r = [lemmatizer.lemmatize(word) for word in r]
    r = ' '.join(r)
    corpus.append(r)

#assign corpus to data['email_text']
data['email_text'] = corpus


#Create Feature and Label sets

X = data['email_text']
y = data['type']

#train test split(70% train -33% test)
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size = 0.2, random_state = 123)

#Train Bag of words model
cv = CountVectorizer()

X_train_cv = cv.fit_transform(X_train)

#Training Logistic Regression Model
lr = LogisticRegression(solver='lbfgs', max_iter=10000)
lr.fit(X_train_cv, y_train)

#Trainingn SVM model
SVM = svm.SVC(C=1.0, kernel = 'linear', degree = 3, gamma= 'auto')
SVM.fit(X_train_cv, y_train)

#Training Random Forest
RFC = RandomForestClassifier()
RFC.fit(X_train_cv, y_train)

# Training Decision Tree
DTC = tree.DecisionTreeClassifier()
DTC.fit(X_train_cv, y_train)

#Training Naive Bayes
Naive = naive_bayes.MultinomialNB()
Naive.fit(X_train_cv, y_train)






