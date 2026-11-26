const message = document.querySelector('#message');
const entryForm = document.querySelector('.entry');
let saving = false;

async function update(path, body) {
  if (saving) return;
  saving = true;
  message.hidden = false;
  message.textContent = '저장 중…';
  try {
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'X-Dashboard-Request': '1' },
      body,
    });
    if (response.ok) {
      location.reload();
      return;
    }
    const errors = {
      400: '입력값을 확인하세요.',
      404: '항목을 찾을 수 없습니다. 페이지를 새로고침하세요.',
      409: '같은 이름의 파일이 이미 있습니다.',
      413: '업로드 크기 제한을 초과했습니다.',
    };
    message.textContent = errors[response.status] || '저장하지 못했습니다.';
  } catch {
    message.textContent = '서버에 연결하지 못했습니다.';
  } finally {
    saving = false;
  }
}

document.querySelector('#upload')?.addEventListener('submit', event => {
  event.preventDefault();
  const file = new FormData(event.currentTarget).get('file');
  update('/api/upload?' + new URLSearchParams({ name: file.name }), file);
});

entryForm?.addEventListener('submit', event => {
  event.preventDefault();
  const form = event.currentTarget;
  update('/api/' + form.dataset.kind, JSON.stringify(Object.fromEntries(new FormData(form))));
});

document.querySelectorAll('[data-edit]').forEach(button => {
  button.addEventListener('click', () => {
    const row = button.closest('article');
    const fields = entryForm.elements;
    fields.namedItem('id').value = row.dataset.id;
    fields.namedItem('title').value = row.querySelector('h3').textContent;
    fields.namedItem('body').value = row.querySelector('p').textContent;
    const date = fields.namedItem('date');
    if (date) date.value = row.querySelector('time').textContent;
    entryForm.querySelector('[type=submit]').textContent = '저장';
    entryForm.querySelector('[type=reset]').hidden = false;
    fields.namedItem('title').focus();
  });
});

entryForm?.addEventListener('reset', () => {
  entryForm.elements.namedItem('id').value = '';
  entryForm.querySelector('[type=submit]').textContent = '추가';
  entryForm.querySelector('[type=reset]').hidden = true;
});

document.querySelectorAll('[data-delete]').forEach(button => {
  button.addEventListener('click', () => {
    if (window.confirm('이 항목을 삭제할까요?')) {
      const id = button.closest('article').dataset.id;
      update('/api/' + entryForm.dataset.kind, JSON.stringify({ delete: id }));
    }
  });
});
