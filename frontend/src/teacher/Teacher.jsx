import { useEffect, useState } from 'react'
import { api } from '../api'
import ConfirmModal from '../components/ConfirmModal'
import SecureFile from '../components/SecureFile'

const emptyForm = { title: '', instructions: '', assignment_content: '', subject_id: '', grade: '', class_section: '', due_date: '', published: true, assignment_scope: 'entire_class', assigned_student_ids: [], assignment_file: null }
const formatDate = (value) => value ? new Date(value).toLocaleDateString() : 'Not set'
const feedbackStatusLabels = { NOT_STARTED: 'No submissions', PENDING_REVIEW: 'Pending review', REVIEWED: 'Reviewed' }

function ReviewPanel({ item, assignment, onSaved }) {
  const [feedback, setFeedback] = useState(item.teacher_feedback || '')
  const [deleteOpen, setDeleteOpen] = useState(false)
  const questions = item.grading?.questions || []
  const save = async (status) => { await api(`/teacher/submissions/${item.id}/review`, { method: 'PATCH', body: JSON.stringify({ teacher_feedback: feedback, feedback_status: status }) }); onSaved() }
  return (
    <article className="review-panel">
      <div>
        <div className="review-assignment-prompt">
          <p className="eyebrow">ASSIGNMENT · {assignment.title}</p>
          {assignment.assignment_content && <p className="ocr-text">{assignment.assignment_content}</p>}
          {assignment.instructions && assignment.instructions !== assignment.assignment_content && (
            <>
              <p className="eyebrow">INSTRUCTIONS</p>
              <p className="ocr-text">{assignment.instructions}</p>
            </>
          )}
        </div>
        <p className="eyebrow">STUDENT ANSWER</p>
        {item.submission_text && <p className="ocr-text">{item.submission_text}</p>}
        {questions.length > 0 && (
          <div className="review-question-list">
            <p className="eyebrow">AI QUESTION REVIEW</p>
            {questions.map((question, index) => (
              <article className="review-question" key={`${item.id}-${index}`}>
                <strong>Question {index + 1}: {question.question || 'Question not identified'}</strong>
                <p><b>Student answer</b>{question.answer || 'No answer identified for this question.'}</p>
                <p><b>Question score</b>{question.is_correct === true ? 'Correct' : question.is_correct === false ? 'Needs review' : 'Not scored individually'}</p>
                {question.feedback && <p><b>Feedback</b>{question.feedback}</p>}
              </article>
            ))}
          </div>
        )}
        <p className="eyebrow">OCR EXTRACTED TEXT</p>
        <p className="ocr-text">{item.ocr_text || 'No text was extracted.'}</p>
        {(item.uploaded_files || []).length > 0 && (
          <>
            <p className="eyebrow">STUDENT UPLOADS</p>
            {item.uploaded_files.map((file) => <SecureFile key={file.url} file={file} />)}
          </>
        )}
      </div>
      <div>
        <div className="review-status-row">
          <p className="eyebrow">AI SCORE · {item.ai_score ?? 'Not scored'}{item.ai_score === null || item.ai_score === undefined ? '' : '%'}</p>
          {item.feedback_status === 'REVIEWED' && <span className="status-indicator complete">Feedback submitted</span>}
        </div>
        <p>{item.ai_feedback}</p>
        {item.feedback_status === 'REVIEWED' && item.teacher_feedback && (
          <div className="teacher-feedback-preview">
            <strong>Submitted teacher feedback</strong>
            <p>{item.teacher_feedback}</p>
          </div>
        )}
        <label>Teacher feedback<textarea value={feedback} onChange={(event) => setFeedback(event.target.value)} rows="4" /></label>
        <div className="review-actions">
          <button className="danger-button" onClick={() => setDeleteOpen(true)}>Delete feedback</button>
          <button className="primary-button" onClick={() => save('REVIEWED')}>{item.feedback_status === 'REVIEWED' ? 'Update feedback' : 'Submit feedback'}</button>
        </div>
      </div>
      <ConfirmModal open={deleteOpen} title="Delete feedback?" message="This will remove your feedback from the submission." onCancel={() => setDeleteOpen(false)} onConfirm={() => save('DELETED')} />
    </article>
  )
}

export default function Teacher() {
  const [tab, setTab] = useState('overview')
  const [dashboard, setDashboard] = useState(null)
  const [assignments, setAssignments] = useState([])
  const [classes, setClasses] = useState([])
  const [subjects, setSubjects] = useState([])
  const [subjectForm, setSubjectForm] = useState({ name: '' })
  const [editingSubjectId, setEditingSubjectId] = useState(null)
  const [newClass, setNewClass] = useState({ grade: '', section: '' })
  const [copiedClassId, setCopiedClassId] = useState('')
  const [form, setForm] = useState(emptyForm)
  const [students, setStudents] = useState([])
  const [assignmentStudents, setAssignmentStudents] = useState([])
  const [editingId, setEditingId] = useState(null)
  const [tracking, setTracking] = useState(null)
  const [error, setError] = useState('')
  const [pendingDelete, setPendingDelete] = useState(null)
  const [assignmentSearch, setAssignmentSearch] = useState('')
  const [assignmentFilters, setAssignmentFilters] = useState({ subject: '', grade: '', section: '', student: '', feedbackStatus: '' })
  const [assignmentPage, setAssignmentPage] = useState(1)
  const [assignmentPageSize, setAssignmentPageSize] = useState(10)
  const [assignmentTotal, setAssignmentTotal] = useState(0)
  const [assignmentPageCount, setAssignmentPageCount] = useState(1)
  const [assignmentLoading, setAssignmentLoading] = useState(false)
  const [assignmentRefresh, setAssignmentRefresh] = useState(0)

  const load = async () => {
    try {
      const [overview, classData, subjectData] = await Promise.all([api('/teacher/dashboard/classroom'), api('/teacher/classes'), api('/teacher/subjects')])
      setDashboard(overview); setClasses(classData.classes); setSubjects(subjectData.subjects)
    } catch (err) { setError(err.message) }
  }
  useEffect(() => { load() }, [])
  useEffect(() => {
    let active = true
    const timer = setTimeout(async () => {
      const params = new URLSearchParams({ page: String(assignmentPage), page_size: String(assignmentPageSize) })
      if (assignmentSearch.trim()) params.set('search', assignmentSearch.trim())
      if (assignmentFilters.subject) params.set('subject', assignmentFilters.subject)
      if (assignmentFilters.grade) params.set('grade', assignmentFilters.grade)
      if (assignmentFilters.section) params.set('section', assignmentFilters.section)
      if (assignmentFilters.student) params.set('student_id', assignmentFilters.student)
      if (assignmentFilters.feedbackStatus) params.set('feedback_status', assignmentFilters.feedbackStatus)
      setAssignmentLoading(true)
      try {
        const data = await api(`/teacher/assignments?${params}`)
        if (!active) return
        setAssignments(data.assignments || [])
        setAssignmentTotal(data.total || 0)
        setAssignmentPageCount(data.page_count || 1)
      } catch (err) { if (active) setError(err.message) } finally { if (active) setAssignmentLoading(false) }
    }, 250)
    return () => { active = false; clearTimeout(timer) }
  }, [assignmentSearch, assignmentFilters, assignmentPage, assignmentPageSize, assignmentRefresh])
  useEffect(() => {
    if (!assignmentFilters.grade || !assignmentFilters.section) { setAssignmentStudents([]); return }
    const params = new URLSearchParams({ grade: assignmentFilters.grade, class_section: assignmentFilters.section })
    if (assignmentFilters.subject) params.set('subject', assignmentFilters.subject)
    api(`/teacher/students?${params}`).then((data) => {
      setAssignmentStudents(data.students || [])
      if (!data.students?.some((student) => student.id === assignmentFilters.student)) {
        setAssignmentFilters((current) => ({ ...current, student: '' }))
      }
    }).catch((err) => setError(err.message))
  }, [assignmentFilters.subject, assignmentFilters.grade, assignmentFilters.section, assignmentFilters.student])
  useEffect(() => {
    if (!form.grade || !form.class_section) { setStudents([]); return }
    api(`/teacher/students?grade=${encodeURIComponent(form.grade)}&class_section=${encodeURIComponent(form.class_section)}`).then((data) => setStudents(data.students)).catch((err) => setError(err.message))
  }, [form.grade, form.class_section])

  const update = (field) => (event) => setForm((current) => ({ ...current, [field]: event.target.type === 'checkbox' ? event.target.checked : event.target.type === 'file' ? event.target.files[0] || null : event.target.value }))
  const createClass = async (event) => { event.preventDefault(); setError(''); try { await api('/teacher/classes', { method: 'POST', body: JSON.stringify(newClass) }); setNewClass({ grade: '', section: '' }); await load() } catch (err) { setError(err.message) } }
  const copyEnrollmentKey = async (item) => { try { await navigator.clipboard.writeText(item.enrollment_key); setCopiedClassId(item.id) } catch { setError('Could not copy the enrollment key. Select and copy it manually.') } }
  const removeClass = async (item) => { try { await api(`/teacher/classes/${item.id}`, { method: 'DELETE' }); await load() } catch (err) { setError(err.message) } }
  const saveSubject = async (event) => { event.preventDefault(); setError(''); try { await api(editingSubjectId ? `/teacher/subjects/${editingSubjectId}` : '/teacher/subjects', { method: editingSubjectId ? 'PATCH' : 'POST', body: JSON.stringify(subjectForm) }); setSubjectForm({ name: '' }); setEditingSubjectId(null); await load() } catch (err) { setError(err.message) } }
  const editSubject = (item) => { setEditingSubjectId(item.id); setSubjectForm({ name: item.name }); setTab('subjects') }
  const removeSubject = async (item) => { try { await api(`/teacher/subjects/${item.id}`, { method: 'DELETE' }); await load() } catch (err) { setError(err.message) } }
  const create = async (event) => {
    event.preventDefault(); setError('')
    try {
      const body = new FormData()
      Object.entries(form).forEach(([key, value]) => { if (key === 'assigned_student_ids') body.append(key, JSON.stringify(value)); else if (key === 'assignment_file' && value) body.append(key, value); else if (key !== 'assignment_file') body.append(key, String(value)) })
      await api(editingId ? `/teacher/assignments/${editingId}` : '/teacher/assignments', { method: editingId ? 'PATCH' : 'POST', body })
      setForm(emptyForm); setEditingId(null); await load(); setAssignmentRefresh((value) => value + 1); setTab('assignments')
    } catch (err) { setError(err.message) }
  }
  const edit = (assignment) => { setEditingId(assignment.id); setForm({ title: assignment.title, instructions: assignment.instructions, assignment_content: assignment.assignment_content || '', subject_id: assignment.subject_id, grade: assignment.grade, class_section: assignment.class_section, due_date: assignment.due_date, published: assignment.published, assignment_scope: assignment.assignment_scope || 'entire_class', assigned_student_ids: assignment.assigned_student_ids || [], assignment_file: null }); setTab('create') }
  const remove = async (assignment) => { try { await api(`/teacher/assignments/${assignment.id}`, { method: 'DELETE' }); await load(); setAssignmentRefresh((value) => value + 1) } catch (err) { setError(err.message) } }
  const askToDelete = (title, message, action) => setPendingDelete({ title, message, action })
  const openTracking = async (assignment) => { try { setTracking(await api(`/teacher/assignments/${assignment.id}/tracking`)); setTab('tracking') } catch (err) { setError(err.message) } }
  const toggleStudent = (id) => setForm((current) => ({ ...current, assigned_student_ids: current.assigned_student_ids.includes(id) ? current.assigned_student_ids.filter((studentId) => studentId !== id) : [...current.assigned_student_ids, id] }))
  const grades = [...new Set(classes.map((item) => item.grade))]
  const sections = classes.filter((item) => item.grade === form.grade).map((item) => item.section)
  const assignmentSubjects = subjects.map((item) => item.name).sort()
  const assignmentGrades = [...new Set(classes.map((item) => item.grade))].sort()
  const assignmentSections = [...new Set(classes.map((item) => item.section))].sort()
  const currentAssignmentPage = Math.min(assignmentPage, assignmentPageCount)
  const visibleAssignments = assignments
  const updateAssignmentFilter = (field) => (event) => { setAssignmentFilters((current) => ({ ...current, [field]: event.target.value })); setAssignmentPage(1) }
  const resetAssignmentFilters = () => { setAssignmentSearch(''); setAssignmentFilters({ subject: '', grade: '', section: '', student: '', feedbackStatus: '' }); setAssignmentPage(1) }

  if (!dashboard) return <main className="workspace"><p className={error ? 'error' : 'loading'}>{error || 'Loading teacher dashboard...'}</p></main>
  const cards = [['students', 'Students'], ['assignments', 'Work'], ['submitted', 'Done'], ['not_submitted', 'Not done'], ['average_score', 'Class score']]
  return <main className="workspace classroom-workspace">
    <div className="page-top"><div><p className="eyebrow">TEACHER DASHBOARD</p></div><div className="date-chip">Classroom live</div></div>
    {error && <p className="error">{error}</p>}
    <nav className="classroom-tabs" aria-label="Teacher pages">{[['overview', 'Overview'], ['classes', 'Classes'], ['subjects', 'Subjects'], ['assignments', 'Assignments'], ['create', editingId ? 'Edit assignment' : 'Create assignment']].map(([key, label]) => <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{label}</button>)}{tracking && <button className={tab === 'tracking' ? 'active' : ''} onClick={() => setTab('tracking')}>Tracking</button>}</nav>
    {tab === 'overview' && <><div className="stats-grid">{cards.map(([key, title]) => <article className="stat-card" key={key}><p>{title}</p><strong>{dashboard.stats[key]}{key === 'average_score' ? '%' : ''}</strong><small>Current classroom view</small></article>)}</div><section className="classroom-section"><p className="eyebrow">ASSIGNMENT PULSE</p><h2>Track every student assignment</h2><div className="assignment-grid">{assignments.slice(0, 3).map((assignment) => <button className="quick-assignment" key={assignment.id} onClick={() => openTracking(assignment)}><span>{assignment.subject}</span><strong>{assignment.title}</strong><small>Grade {assignment.grade} · Section {assignment.class_section}</small></button>)}</div></section></>}
      {tab === 'classes' && <section className="classroom-section"><p className="eyebrow">CLASS MANAGEMENT</p><h2>Available classes</h2><form className="class-create-form" onSubmit={createClass}><label>Grade<select required value={newClass.grade} onChange={(event) => setNewClass({ ...newClass, grade: event.target.value })}><option value="">Choose a grade</option>{Array.from({ length: 10 }, (_, index) => String(index + 1)).map((grade) => <option key={grade} value={grade}>{grade}</option>)}</select></label><label>Section<select required value={newClass.section} onChange={(event) => setNewClass({ ...newClass, section: event.target.value })}><option value="">Choose a section</option>{['A', 'B', 'C'].map((section) => <option key={section} value={section}>{section}</option>)}</select></label><button className="primary-button" type="submit">Create class <span>→</span></button></form><div className="class-list section-scroll">{classes.map((item) => <div className="class-row" key={item.id}><strong>Grade {item.grade}</strong><span>Section {item.section}</span><span>Enrollment key: <code>{item.enrollment_key}</code></span><button className="secondary-button" type="button" onClick={() => copyEnrollmentKey(item)}>{copiedClassId === item.id ? 'Copied' : 'Copy key'}</button><button className="danger-button" type="button" onClick={() => askToDelete(`Delete Grade ${item.grade}?`, `This will remove Section ${item.section} from your classroom.`, () => removeClass(item))}>Delete</button></div>)}</div></section>}
        {tab === 'subjects' && <section className="classroom-section"><p className="eyebrow">SUBJECT MANAGEMENT</p><h2>Official subjects</h2><form className="class-create-form" onSubmit={saveSubject}><label>Subject name<input required value={subjectForm.name} onChange={(event) => setSubjectForm({ name: event.target.value })} /></label><button className="primary-button" type="submit">{editingSubjectId ? 'Save subject' : 'Create subject'} <span>→</span></button>{editingSubjectId && <button className="secondary-button" type="button" onClick={() => { setEditingSubjectId(null); setSubjectForm({ name: '' }) }}>Cancel</button>}</form><div className="class-list section-scroll">{subjects.map((item) => <div className="class-row" key={item.id}><strong>{item.name}</strong><span>Official subject</span><div><button className="edit-button" type="button" onClick={() => editSubject(item)}>Edit</button><button className="danger-button" type="button" onClick={() => askToDelete(`Delete ${item.name}?`, 'This will remove the subject from your classroom.', () => removeSubject(item))}>Delete</button></div></div>)}</div></section>}
      {tab === 'assignments' && <section className="classroom-section"><div className="section-title"><div><p className="eyebrow">ASSIGNMENT MANAGEMENT</p><h2>Published classroom work</h2></div><span>{assignmentLoading ? 'Loading...' : `${assignments.length} of ${assignmentTotal} shown`}</span></div><div className="assignment-filters"><label className="assignment-search">Search assignments<input type="search" placeholder="Title, subject, grade, or section" value={assignmentSearch} onChange={(event) => { setAssignmentSearch(event.target.value); setAssignmentPage(1) }} /></label><label>Subject<select value={assignmentFilters.subject} onChange={updateAssignmentFilter('subject')}><option value="">All subjects</option>{assignmentSubjects.map((subject) => <option key={subject}>{subject}</option>)}</select></label><label>Grade<select value={assignmentFilters.grade} onChange={updateAssignmentFilter('grade')}><option value="">All grades</option>{assignmentGrades.map((grade) => <option key={grade}>{grade}</option>)}</select></label><label>Section<select value={assignmentFilters.section} onChange={updateAssignmentFilter('section')}><option value="">All sections</option>{assignmentSections.map((section) => <option key={section}>{section}</option>)}</select></label><label>Student<select value={assignmentFilters.student} onChange={updateAssignmentFilter('student')} disabled={!assignmentFilters.grade || !assignmentFilters.section}><option value="">All students</option>{assignmentStudents.map((student) => <option key={student.id} value={student.id}>{student.name}</option>)}</select></label><label>Feedback status<select value={assignmentFilters.feedbackStatus} onChange={updateAssignmentFilter('feedbackStatus')}><option value="">All statuses</option>{Object.entries(feedbackStatusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><button className="secondary-button filter-reset" type="button" onClick={resetAssignmentFilters}>Clear filters</button></div>{visibleAssignments.length ? <div className="section-scroll assignment-list">{visibleAssignments.map((assignment) => <article className="teacher-assignment" key={assignment.id}><div><span className="subject-tag">{assignment.subject}</span><h3>{assignment.title}</h3><p>Grade {assignment.grade} · Section {assignment.class_section} · Due {formatDate(assignment.due_date)}</p><span className={`status-indicator ${assignment.feedback_status === 'REVIEWED' ? 'complete' : assignment.feedback_status === 'PENDING_REVIEW' ? 'pending' : 'missing'}`}>{feedbackStatusLabels[assignment.feedback_status] || 'No submissions'}</span></div><div className="teacher-actions"><button className="primary-link" onClick={() => openTracking(assignment)}>Track students</button><button className="edit-button" onClick={() => edit(assignment)}>Edit</button><button className="danger-button" onClick={() => askToDelete(`Delete ${assignment.title}?`, 'This will permanently remove the assignment and its classroom tracking.', () => remove(assignment))}>Delete</button></div></article>)}</div> : <div className="empty-state">No assignments match these filters.</div>}<div className="assignment-pagination"><label>Items per page<select value={assignmentPageSize} onChange={(event) => { setAssignmentPageSize(Number(event.target.value)); setAssignmentPage(1) }}><option value="10">10</option><option value="25">25</option><option value="50">50</option></select></label><span>Page {currentAssignmentPage} of {assignmentPageCount}</span><button className="secondary-button" type="button" disabled={currentAssignmentPage === 1} onClick={() => setAssignmentPage((page) => page - 1)}>Previous</button><button className="secondary-button" type="button" disabled={currentAssignmentPage >= assignmentPageCount} onClick={() => setAssignmentPage((page) => page + 1)}>Next</button></div></section>}
    {tab === 'create' && <section className="classroom-section"><p className="eyebrow">{editingId ? 'EDIT ASSIGNMENT' : 'NEW ASSIGNMENT'}</p><h2>Set the work your class needs.</h2><form className="assignment-create-form" onSubmit={create}><label>Title<input required value={form.title} onChange={update('title')} /></label><label>Subject<select required value={form.subject_id} onChange={update('subject_id')}><option value="">Choose a subject</option>{subjects.map((subject) => <option key={subject.id} value={subject.id}>{subject.name}</option>)}</select></label><div className="form-columns"><label>Grade<select required value={form.grade} onChange={(event) => setForm({ ...form, grade: event.target.value, class_section: '' })}><option value="">Choose a grade</option>{grades.map((grade) => <option key={grade}>{grade}</option>)}</select></label><label>Section<select required disabled={!form.grade} value={form.class_section} onChange={update('class_section')}><option value="">Choose a section</option>{sections.map((section) => <option key={section}>{section}</option>)}</select></label><label>Due date<input required type="date" value={form.due_date} onChange={update('due_date')} /></label></div><label>Assignment Questions / Content<textarea rows="8" value={form.assignment_content} onChange={update('assignment_content')} /></label><label>Instructions<textarea rows="4" value={form.instructions} onChange={update('instructions')} /></label><label>Upload Assignment File<input type="file" accept="image/*,.pdf" onChange={update('assignment_file')} /></label><fieldset className="assignment-target"><legend>Assign to</legend><label className="radio-label"><input type="radio" name="scope" value="entire_class" checked={form.assignment_scope === 'entire_class'} onChange={update('assignment_scope')} /> Entire Class</label><label className="radio-label"><input type="radio" name="scope" value="selected_students" checked={form.assignment_scope === 'selected_students'} onChange={update('assignment_scope')} /> Selected Students</label>{form.assignment_scope === 'selected_students' && students.map((student) => <label className="student-option" key={student.id}><input type="checkbox" checked={form.assigned_student_ids.includes(student.id)} onChange={() => toggleStudent(student.id)} /> {student.name}</label>)}</fieldset><label className="checkbox-label"><input type="checkbox" checked={form.published} onChange={update('published')} /> Publish for this class</label><button className="primary-button" type="submit">{editingId ? 'Save changes' : 'Publish assignment'} <span>→</span></button></form></section>}
    {tab === 'tracking' && tracking && <section className="classroom-section"><button className="back-button" onClick={() => setTab('assignments')}>← Assignments</button><p className="eyebrow">ASSIGNMENT TRACKING</p><h2>{tracking.assignment.title}</h2>{tracking.assignment.assignment_file && <div className="assignment-resource"><p className="eyebrow">ASSIGNMENT FILE</p><SecureFile file={tracking.assignment.assignment_file} /></div>}<div className="tracking-list section-scroll">{tracking.students.map(({ student, submission }) => <article className="tracking-row" key={student.id}><div><strong>{student.name}</strong></div><span className={`status-indicator ${submission ? submission.feedback_status === 'REVIEWED' ? 'complete' : 'pending' : 'missing'}`}>{submission ? submission.feedback_status === 'REVIEWED' ? 'Submitted / Completed' : 'Pending feedback' : 'Not done'}</span>{submission && <details><summary>Review submission</summary><ReviewPanel item={submission} assignment={tracking.assignment} onSaved={() => openTracking(tracking.assignment)} /></details>}</article>)}</div></section>}
    <ConfirmModal open={Boolean(pendingDelete)} title={pendingDelete?.title} message={pendingDelete?.message} onCancel={() => setPendingDelete(null)} onConfirm={async () => { await pendingDelete?.action(); setPendingDelete(null) }} />
  </main>
}
