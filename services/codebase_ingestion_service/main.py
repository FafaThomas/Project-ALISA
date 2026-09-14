from service import CodebaseIngestionService

# GitLab migration test
# Gitlab migration test 2
PROJECT = r"C:\Users\tdecl\Downloads\ICTI_Website-main\ICTI_Website-main"

ingestion = CodebaseIngestionService()

ingestion.create_project(PROJECT)