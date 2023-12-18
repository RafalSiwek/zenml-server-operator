
import logging
import os
from zenml.utils import io_utils
import tempfile

import numpy as np
import tensorflow as tf

from typing import Annotated, Optional, Tuple
from zenml.integrations.constants import TENSORFLOW
from zenml.pipelines import pipeline
from zenml import get_step_context, pipeline, step
from zenml.config import DockerSettings


@step
def importer() -> Tuple[
    Annotated[np.ndarray, 'X_train'],
    Annotated[np.ndarray, 'X_test'],
    Annotated[np.ndarray, 'y_train'],
    Annotated[np.ndarray, 'y_test'],
    ]:
    """Download the MNIST data store it as an artifact"""
    (X_train, y_train), (
        X_test,
        y_test,
    ) = tf.keras.datasets.mnist.load_data()
    return X_train, X_test, y_train, y_test


@step
def normalizer(
    X_train: np.ndarray, X_test: np.ndarray
) -> Tuple[
    Annotated[np.ndarray, 'X_train_normed'],
    Annotated[np.ndarray, 'X_test_normed']
    ]:
    """Normalize digits dataset with mean and standard deviation."""
    X_train_normed = (X_train - np.mean(X_train)) / np.std(X_train)
    X_test_normed = (X_test - np.mean(X_test)) / np.std(X_test)
    return X_train_normed, X_test_normed


@step
def trainer(
    X_train: np.ndarray,
    y_train: np.ndarray,
    epochs: int = 1,
    lr: float = 0.001
) -> tf.keras.Model:
    """Train a neural net from scratch to recognize MNIST digits return our
    model or the learner"""

    model = tf.keras.Sequential(
        [
            tf.keras.layers.Flatten(input_shape=(28, 28)),
            tf.keras.layers.Dense(10, activation="relu"),
            tf.keras.layers.Dense(10),
        ]
    )

    
    artifact_dir = os.path.join(get_step_context().get_output_artifact_uri(), "logs")

    with tempfile.TemporaryDirectory() as tempdir:

        tensorboard_callback = tf.keras.callbacks.TensorBoard(
            log_dir=tempdir, histogram_freq=1
        )

        model.compile(
            optimizer=tf.keras.optimizers.Adam(lr),
            loss=tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True),
            metrics=["accuracy"],
        )

        model.fit(
            X_train,
            y_train,
            epochs=epochs,
            callbacks=[tensorboard_callback],
        )

        io_utils.copy_dir(tempdir, artifact_dir, overwrite=True)

        return model


@step
def evaluator(
    X_test: np.ndarray,
    y_test: np.ndarray,
    model: tf.keras.Model,
) -> float:
    """Calculate the accuracy on the test set"""

    _, test_acc = model.evaluate(X_test, y_test, verbose=2)
    logging.info(f"Test accuracy: {test_acc}")
    return test_acc


docker_settings = DockerSettings(required_integrations=[TENSORFLOW])

@pipeline(enable_cache=True, settings={"docker": docker_settings})
def mnist_pipeline():
    # Link all the steps together
    X_train, X_test, y_train, y_test = importer()
    X_trained_normed, X_test_normed = normalizer(X_train=X_train, X_test=X_test)
    model = trainer(X_train=X_trained_normed, y_train=y_train)
    evaluator(X_test=X_test_normed, y_test=y_test, model=model)


if __name__ == "__main__":
    mnist_pipeline()