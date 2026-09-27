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
                bat '"C:\\Users\\Samyuktha Jannu\\AppData\\Local\\Programs\\Python\\Python310\\python.exe" -m pip install -r requirements.txt'
            }
        }

        stage('Test') {
            steps {
                bat '"C:\\Users\\Samyuktha Jannu\\AppData\\Local\\Programs\\Python\\Python310\\python.exe" -m pytest -q'
            }
        }

        stage('Docker Build') {
            steps {
                bat 'docker build -t bugtrack:latest .'
            }
        }
    }
}
