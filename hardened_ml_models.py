# filename: hardened_ml_models.py
"""
Hardened ML model management with validation and secure loading.
Provides safe model loading, validation, and caching capabilities.
"""

import os
import pickle
import hashlib
import json
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
import pandas as pd
import numpy as np
import nltk
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
from sklearn.ensemble import RandomForestClassifier
from sklearn.tree import DecisionTreeClassifier
from sklearn.naive_bayes import MultinomialNB
from sklearn.metrics import accuracy_score
import re
from nltk.stem import WordNetLemmatizer
from config_loader import get_config
from logger_setup import get_logger
from statistics import mode

logger = get_logger(__name__)

class ModelValidationError(Exception):
    """Exception raised when model validation fails."""
    pass

class HardenedMLModels:
    """Secure ML model management with validation and integrity checks."""
    
    def __init__(self):
        self.config = get_config()
        self.model_cache_dir = Path(self.config.ml_models.model_cache_dir)
        self.model_cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Model components
        self.vectorizer: Optional[CountVectorizer] = None
        self.models: Dict[str, Any] = {}
        self.lemmatizer: Optional[WordNetLemmatizer] = None
        
        # Model metadata
        self.model_metadata = {}
        self.is_initialized = False
    
    def _calculate_file_hash(self, filepath: Path) -> str:
        """Calculate SHA256 hash of a file."""
        hash_sha256 = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()
    
    def _save_model_metadata(self, metadata: Dict[str, Any]):
        """Save model training metadata."""
        metadata_file = self.model_cache_dir / "model_metadata.json"
        try:
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            logger.info("Model metadata saved")
        except Exception as e:
            logger.error(f"Failed to save model metadata: {e}")
    
    def _load_model_metadata(self) -> Dict[str, Any]:
        """Load model training metadata."""
        metadata_file = self.model_cache_dir / "model_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load model metadata: {e}")
        return {}
    
    def _validate_dataset(self, dataset_path: Path) -> Tuple[bool, Dict[str, Any]]:
        """Validate dataset integrity and structure."""
        try:
            if not dataset_path.exists():
                return False, {"error": "Dataset file not found"}
            
            # Calculate dataset hash for integrity checking
            dataset_hash = self._calculate_file_hash(dataset_path)
            
            # Load and validate dataset
            df = pd.read_csv(dataset_path, encoding='unicode_escape')
            
            required_columns = ['email_text', 'type']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            if missing_columns:
                return False, {"error": f"Missing columns: {missing_columns}"}
            
            # Check for empty values
            empty_text = df['email_text'].isna().sum()
            empty_type = df['type'].isna().sum()
            
            # Get dataset statistics
            stats = {
                "total_rows": len(df),
                "empty_text_rows": int(empty_text),
                "empty_type_rows": int(empty_type),
                "unique_types": df['type'].unique().tolist(),
                "type_distribution": df['type'].value_counts().to_dict(),
                "dataset_hash": dataset_hash
            }
            
            # Validation criteria
            min_rows = 50  # Minimum number of samples
            max_empty_ratio = 0.1  # Maximum 10% empty values
            
            validation_passed = (
                len(df) >= min_rows and
                (empty_text / len(df)) <= max_empty_ratio and
                (empty_type / len(df)) <= max_empty_ratio and
                len(df['type'].unique()) >= 2  # At least 2 classes
            )
            
            return validation_passed, stats
            
        except Exception as e:
            return False, {"error": str(e)}
    
    def _preprocess_text(self, text: str) -> str:
        """Preprocess text for model training/prediction."""
        try:
            # Convert to lowercase
            text = text.lower()
            
            # Remove punctuation and digits, keep only alphabetic characters and spaces
            text = ''.join(c for c in text if c.isalpha() or c.isspace())
            
            # Tokenize
            tokens = nltk.word_tokenize(text)
            
            # Lemmatize
            if self.lemmatizer is None:
                self.lemmatizer = WordNetLemmatizer()
            
            tokens = [self.lemmatizer.lemmatize(word) for word in tokens]
            
            return ' '.join(tokens)
            
        except Exception as e:
            logger.error(f"Text preprocessing failed: {e}")
            return text  # Return original text as fallback
    
    def _train_models(self, X_train, y_train) -> Dict[str, Any]:
        """Train all ML models and return performance metrics."""
        models_config = {
            'logistic_regression': LogisticRegression(solver='lbfgs', max_iter=10000),
            'svm': SVC(C=1.0, kernel='linear', degree=3, gamma='auto'),
            'random_forest': RandomForestClassifier(n_estimators=100, random_state=42),
            'decision_tree': DecisionTreeClassifier(random_state=42),
            'naive_bayes': MultinomialNB()
        }
        
        trained_models = {}
        performance_metrics = {}
        
        for model_name, model in models_config.items():
            try:
                logger.info(f"Training {model_name}...")
                model.fit(X_train, y_train)
                trained_models[model_name] = model
                
                # Calculate training accuracy
                train_pred = model.predict(X_train)
                train_accuracy = accuracy_score(y_train, train_pred)
                performance_metrics[model_name] = {
                    'train_accuracy': float(train_accuracy)
                }
                
                logger.info(f"{model_name} training accuracy: {train_accuracy:.4f}")
                
            except Exception as e:
                logger.error(f"Failed to train {model_name}: {e}")
                continue
        
        return trained_models, performance_metrics
    
    def _save_models(self, models: Dict[str, Any], vectorizer: CountVectorizer, 
                    metadata: Dict[str, Any]):
        """Save trained models and vectorizer."""
        try:
            # Save vectorizer
            vectorizer_file = self.model_cache_dir / "vectorizer.pkl"
            with open(vectorizer_file, 'wb') as f:
                pickle.dump(vectorizer, f)
            
            # Save individual models
            for model_name, model in models.items():
                model_file = self.model_cache_dir / f"{model_name}.pkl"
                with open(model_file, 'wb') as f:
                    pickle.dump(model, f)
            
            # Save metadata
            self._save_model_metadata(metadata)
            
            logger.info(f"Saved {len(models)} models and vectorizer")
            
        except Exception as e:
            logger.error(f"Failed to save models: {e}")
            raise
    
    def _load_models(self) -> bool:
        """Load trained models from cache."""
        try:
            # Load vectorizer
            vectorizer_file = self.model_cache_dir / "vectorizer.pkl"
            if not vectorizer_file.exists():
                return False
            
            with open(vectorizer_file, 'rb') as f:
                self.vectorizer = pickle.load(f)
            
            # Load models
            model_names = ['logistic_regression', 'svm', 'random_forest', 'decision_tree', 'naive_bayes']
            
            for model_name in model_names:
                model_file = self.model_cache_dir / f"{model_name}.pkl"
                if model_file.exists():
                    with open(model_file, 'rb') as f:
                        self.models[model_name] = pickle.load(f)
            
            # Load metadata
            self.model_metadata = self._load_model_metadata()
            
            # Validate loaded models
            if len(self.models) == 0:
                return False
            
            logger.info(f"Loaded {len(self.models)} models from cache")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load models: {e}")
            return False
    
    def _models_need_retraining(self, dataset_hash: str) -> bool:
        """Check if models need retraining based on dataset changes."""
        if not self.model_metadata:
            return True
        
        # Check if dataset hash changed
        stored_hash = self.model_metadata.get('dataset_hash', '')
        if stored_hash != dataset_hash:
            logger.info("Dataset changed, models need retraining")
            return True
        
        # Check model performance
        min_accuracy = self.config.ml_models.min_accuracy_threshold
        for model_name, metrics in self.model_metadata.get('performance', {}).items():
            if metrics.get('train_accuracy', 0) < min_accuracy:
                logger.warning(f"Model {model_name} accuracy below threshold, retraining needed")
                return True
        
        return False
    
    def initialize(self, force_retrain: bool = False) -> bool:
        """Initialize ML models with validation."""
        try:
            logger.info("Initializing ML models...")
            
            # Download required NLTK data
            try:
                nltk.data.find('tokenizers/punkt')
                nltk.data.find('corpora/wordnet')
            except LookupError:
                logger.info("Downloading required NLTK data...")
                nltk.download('punkt', quiet=True)
                nltk.download('wordnet', quiet=True)
            
            # Validate dataset
            dataset_path = Path(self.config.ml_models.dataset_path)
            dataset_valid, dataset_stats = self._validate_dataset(dataset_path)
            
            if not dataset_valid:
                raise ModelValidationError(f"Dataset validation failed: {dataset_stats.get('error', 'Unknown error')}")
            
            logger.info(f"Dataset validation passed: {dataset_stats['total_rows']} samples")
            
            # Check if we need to retrain models
            dataset_hash = dataset_stats['dataset_hash']
            need_retraining = force_retrain or self._models_need_retraining(dataset_hash)
            
            if not need_retraining and self._load_models():
                logger.info("Using cached models")
                self.is_initialized = True
                return True
            
            # Train new models
            logger.info("Training new models...")
            
            # Load and preprocess dataset
            df = pd.read_csv(dataset_path, encoding='unicode_escape')
            
            # Preprocess text
            logger.info("Preprocessing text data...")
            corpus = []
            for text in df['email_text']:
                processed_text = self._preprocess_text(str(text))
                corpus.append(processed_text)
            
            # Prepare features and labels
            X = corpus
            y = df['type']
            
            # Split data
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.2, random_state=42, stratify=y
            )
            
            # Train vectorizer
            logger.info("Training vectorizer...")
            self.vectorizer = CountVectorizer()
            X_train_vectorized = self.vectorizer.fit_transform(X_train)
            X_test_vectorized = self.vectorizer.transform(X_test)
            
            # Train models
            trained_models, performance_metrics = self._train_models(X_train_vectorized, y_train)
            
            if not trained_models:
                raise ModelValidationError("No models were successfully trained")
            
            # Evaluate on test set
            for model_name, model in trained_models.items():
                try:
                    test_pred = model.predict(X_test_vectorized)
                    test_accuracy = accuracy_score(y_test, test_pred)
                    performance_metrics[model_name]['test_accuracy'] = float(test_accuracy)
                    
                    logger.info(f"{model_name} test accuracy: {test_accuracy:.4f}")
                    
                    # Check minimum accuracy threshold
                    if test_accuracy < self.config.ml_models.min_accuracy_threshold:
                        logger.warning(f"{model_name} accuracy {test_accuracy:.4f} below threshold {self.config.ml_models.min_accuracy_threshold}")
                
                except Exception as e:
                    logger.error(f"Failed to evaluate {model_name}: {e}")
            
            # Save models
            metadata = {
                'dataset_hash': dataset_hash,
                'dataset_stats': dataset_stats,
                'performance': performance_metrics,
                'training_date': pd.Timestamp.now().isoformat(),
                'model_count': len(trained_models)
            }
            
            self._save_models(trained_models, self.vectorizer, metadata)
            
            # Update instance variables
            self.models = trained_models
            self.model_metadata = metadata
            self.is_initialized = True
            
            logger.info("ML models initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize ML models: {e}")
            return False
    
    def predict(self, text: str) -> str:
        """Make prediction using ensemble of models."""
        if not self.is_initialized:
            raise RuntimeError("Models not initialized. Call initialize() first.")
        
        try:
            # Preprocess text
            processed_text = self._preprocess_text(text)
            
            # Vectorize
            text_vectorized = self.vectorizer.transform([processed_text])
            
            # Get predictions from multiple models
            predictions = []
            model_names = ['svm', 'decision_tree', 'random_forest']  # Use best performing models
            
            for model_name in model_names:
                if model_name in self.models:
                    try:
                        pred = self.models[model_name].predict(text_vectorized)[0]
                        predictions.append(pred)
                    except Exception as e:
                        logger.warning(f"Model {model_name} prediction failed: {e}")
            
            if not predictions:
                raise RuntimeError("No models available for prediction")
            
            # Use majority voting
            final_prediction = mode(predictions)
            
            logger.debug(f"Predictions: {predictions}, Final: {final_prediction}")
            
            return final_prediction
            
        except Exception as e:
            logger.error(f"Prediction failed: {e}")
            raise
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get information about loaded models."""
        return {
            'initialized': self.is_initialized,
            'model_count': len(self.models),
            'available_models': list(self.models.keys()),
            'metadata': self.model_metadata
        }
    
    def validate_models(self) -> bool:
        """Validate that models are working correctly."""
        if not self.is_initialized:
            return False
        
        try:
            # Test with sample texts
            test_cases = [
                "System alarm: critical temperature exceeded",
                "Information: system status normal"
            ]
            
            for test_text in test_cases:
                prediction = self.predict(test_text)
                if prediction not in ['Alarm', 'Information', 'Warning']:
                    logger.warning(f"Unexpected prediction: {prediction}")
                    return False
            
            logger.info("Model validation passed")
            return True
            
        except Exception as e:
            logger.error(f"Model validation failed: {e}")
            return False

# Global model instance
_ml_models: Optional[HardenedMLModels] = None

def get_ml_models() -> HardenedMLModels:
    """Get global ML models instance."""
    global _ml_models
    if _ml_models is None:
        _ml_models = HardenedMLModels()
    return _ml_models

def initialize_models(force_retrain: bool = False) -> bool:
    """Initialize global ML models."""
    models = get_ml_models()
    return models.initialize(force_retrain)

def predict_email_category(text: str) -> str:
    """Predict email category using trained models."""
    models = get_ml_models()
    return models.predict(text)