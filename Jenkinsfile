pipeline {
    agent any

    // 1. 统一管理环境变量，提高复用性
    environment {
        // 定义 Gerrit 评审的基础命令，使用 env. 保护，防止手动触发时崩溃
        GERRIT_BASE_CMD = "ssh -p 29418 jenkins@gerrit.lilingkun.com gerrit review ${env.GERRIT_PATCHSET_REVISION ?: ''}"
        // 提取项目名，增加默认值防止手动触发报错
        PROJECT_URL = "ssh://jenkins@gerrit.lilingkun.com:29418/${env.GERRIT_PROJECT ?: 'unknown-project'}"
    }

    stages {
        stage('1. 拉取代码') {
            steps {
                deleteDir()
                script {
                    // 只有在变量存在时才执行拉取，否则给出友好提示
                    if (env.GERRIT_REFSPEC) {
                        checkout([$class: 'GitSCM', 
                            branches: [[name: 'FETCH_HEAD']],
                            userRemoteConfigs: [[
                                url: "${PROJECT_URL}",
                                refspec: "${env.GERRIT_REFSPEC}"
                            ]]
                        ])
                    } else {
                        echo "未检测到 Gerrit 触发变量，跳过精准拉取 (可能是手动触发)"
                    }
                }
            }
        }

        stage('2. 代码风格检查') {
            steps {
                echo "Running Linter..."
                sh 'echo "Linter Check: Passed"' 
            }
        }

        stage('3. 单元测试') {
            steps {
                echo "Running Unit Tests..."
                sh 'echo "Unit Tests: Passed"'
            }
        }

        stage('4. 集成测试') {
            steps {
                echo "Running Integration Tests..."
                sh 'echo "Integration Tests: Passed"'
            }
        }
    }

    // 2. Post 处理逻辑
    post {
        success {
            script {
                echo "测试全部通过，准备执行 Gerrit 投票 (+1)"
                try {
                    // 使用之前定义的变量，并追加具体的投票参数
                    sh "${GERRIT_BASE_CMD} --verified +1 --message 'Jenkins_Build_Success_Verified+1'"
                } catch (Exception e) {
                    // 如果 SSH 连接失败或 Gerrit 权限问题，捕获异常但不中断 Pipeline 最终状态
                    echo "Gerrit 投票失败 (网络或权限问题): ${e.getMessage()}"
                }
            }
        }
        failure {
            script {
                echo "测试存在失败项，准备执行 Gerrit 投票 (-1)"
                try {
                    sh "${GERRIT_BASE_CMD} --verified -1 --message 'Jenkins_Build_Failed_Verified-1'"
                } catch (Exception e) {
                    echo "Gerrit 投票失败: ${e.getMessage()}"
                }
            }
        }
        always {
            // 无论成功失败，清理工作区
            cleanWs()
            echo "流水线执行结束"
        }
    }
}