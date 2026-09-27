pipeline {
    agent any

    stages {
        stage('Source') {
            steps {
                checkout scm
            }
        }

        stage('Install') {
            steps {
                bat 'python -m pip install -r requirements.txt'
            }
        }

        stage('Test') {
            steps {
                bat 'python -m pytest -q'
            }
        }

        stage('Docker Build') {
            steps {
                bat 'docker build -t bugtrack:latest .'
            }
        }
    }
}
