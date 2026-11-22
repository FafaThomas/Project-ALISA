from coding_agent.structural_verifier import analyze_workspace


def test_detects_missing_destructured_react_component_prop(tmp_path):
    (tmp_path / "TaskItem.jsx").write_text(
        "const TaskItem = ({ task, onDelete }) => <button onClick={onDelete}>{task.title}</button>;\n",
        encoding="utf-8",
    )
    (tmp_path / "TaskList.jsx").write_text(
        "import TaskItem from './TaskItem';\nconst TaskList = ({ tasks, setTasks }) => tasks.map(task => <TaskItem key={task.id} task={task} tasks={tasks} setTasks={setTasks} />);\n",
        encoding="utf-8",
    )

    issues = analyze_workspace(tmp_path)

    assert len(issues) == 1
    assert issues[0].code == "react-missing-component-prop"
    assert "onDelete" in issues[0].message


def test_accepts_matching_component_props(tmp_path):
    (tmp_path / "TaskItem.jsx").write_text(
        "const TaskItem = ({ task, onDelete }) => <button onClick={onDelete}>{task.title}</button>;\n",
        encoding="utf-8",
    )
    (tmp_path / "TaskList.jsx").write_text(
        "const TaskList = ({ tasks, remove }) => tasks.map(task => <TaskItem task={task} onDelete={() => remove(task.id)} />);\n",
        encoding="utf-8",
    )

    assert analyze_workspace(tmp_path) == ()


def test_skips_spread_props_to_avoid_false_positive(tmp_path):
    (tmp_path / "TaskItem.jsx").write_text(
        "const TaskItem = ({ task, onDelete }) => <button onClick={onDelete}>{task.title}</button>;\n",
        encoding="utf-8",
    )
    (tmp_path / "TaskList.jsx").write_text(
        "const TaskList = ({ props }) => <TaskItem {...props} />;\n",
        encoding="utf-8",
    )

    assert analyze_workspace(tmp_path) == ()
