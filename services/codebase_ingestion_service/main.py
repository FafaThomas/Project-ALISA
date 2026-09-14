from service import CodebaseIngestionService

# GitLab migration test
PROJECT = r"C:\Users\tdecl\Downloads\ICTI_Website-main\ICTI_Website-main"

ingestion = CodebaseIngestionService()

ingestion.create_project(PROJECT)