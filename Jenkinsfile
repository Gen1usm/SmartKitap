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
                sh 'docker build --pull -t "$IMAGE_NAME" .'
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

        stage('Trivy Security Scan') {
            steps {
                sh '''
                    trivy image \
                      --timeout 15m \
                      --no-progress \
                      --scanners vuln \
                      --severity HIGH,CRITICAL \
                      --exit-code 0 \
                      --format table \
                      --output trivy-report.txt \
                      "$IMAGE_NAME"

                    cat trivy-report.txt
                '''
            }
        }

        stage('Security Gate') {
            steps {
                sh '''
                    trivy image \
                      --timeout 15m \
                      --no-progress \
                      --scanners vuln \
                      --severity CRITICAL \
                      --exit-code 1 \
                      "$IMAGE_NAME"

                    trivy image \
                      --timeout 15m \
                      --no-progress \
                      --scanners vuln \
                      --severity HIGH \
                      --ignore-unfixed \
                      --exit-code 1 \
                      "$IMAGE_NAME"
                '''
            }
        }

              stage('Deploy') {
            steps {
                withCredentials([
                    sshUserPrivateKey(
                        credentialsId: 'smartkitap-deploy-ssh',
                        keyFileVariable: 'SSH_KEY',
                        usernameVariable: 'SSH_USER'
                    )
                ]) {
                    sh '''
                        ssh -i "$SSH_KEY" \
                          -o BatchMode=yes \
                          -o IdentitiesOnly=yes \
                          -o StrictHostKeyChecking=yes \
                          "$SSH_USER@172.17.0.1" \
                          "$IMAGE_NAME"
                    '''
                }
            }
        }
    }

    post {
        always {
            archiveArtifacts(
                artifacts: 'trivy-report.txt',
                allowEmptyArchive: true
            )
        }

        success {
            echo 'SmartKitap CI and Security Gate: SUCCESS'
        }

        failure {
            echo 'SmartKitap Pipeline: FAILED'
        }
    }
}
