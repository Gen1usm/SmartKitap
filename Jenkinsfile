pipeline {
    agent any

    options {
        skipDefaultCheckout(true)
        timestamps()
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '10'))
    }

    environment {
        IMAGE_NAME = "smartkitap:ci-${BUILD_NUMBER}"
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Build') {
            steps {
                sh 'docker build -t "$IMAGE_NAME" .'
            }
        }

        stage('Check') {
            steps {
                sh '''
                    docker run --rm \
                      -e SECRET_KEY=ci-test-key \
                      "$IMAGE_NAME" \
                      python manage.py check
                '''
            }
        }

        stage('Test') {
            steps {
                sh '''
                    docker run --rm \
                      -e SECRET_KEY=ci-test-key \
                      "$IMAGE_NAME" \
                      python manage.py test --noinput
                '''
            }
        }
    }

    post {
        success {
            echo 'SmartKitap CI: SUCCESS'
        }
        failure {
            echo 'SmartKitap CI: FAILED'
        }
    }
}
