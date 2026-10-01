(function () {
  const nicknameKey = 'iot-admin-nickname';
  const avatarKey = 'iot-admin-avatar';
  const preview = document.getElementById('avatar-preview');
  const fileInput = document.getElementById('avatar-input');
  const nicknameInput = document.getElementById('nickname-input');
  const errorBox = document.getElementById('profile-error');
  const saveButton = document.getElementById('save-profile');
  if (!preview || !fileInput || !nicknameInput || !errorBox || !saveButton) return;

  const dialog = document.getElementById('admin-profile-dialog');
  const openButton = document.getElementById('topbar-user-link');
  const closeButton = document.getElementById('profile-dialog-close');
  const cancelButton = document.getElementById('profile-cancel');
  let avatarData = localStorage.getItem(avatarKey) || '';

  function renderAvatar() {
    if (avatarData) {
      preview.textContent = '';
      preview.classList.add('has-image');
      preview.style.backgroundImage = `url("${avatarData}")`;
    } else {
      preview.textContent = '管';
      preview.classList.remove('has-image');
      preview.style.backgroundImage = '';
    }
  }

  function loadForm() {
    nicknameInput.value = localStorage.getItem(nicknameKey) || '管理员';
    errorBox.textContent = '';
    renderAvatar();
  }

  function openDialog() {
    loadForm();
    if (dialog) dialog.showModal();
  }

  function closeDialog() {
    if (dialog) dialog.close();
  }

  openButton?.addEventListener('click', openDialog);
  closeButton?.addEventListener('click', closeDialog);
  cancelButton?.addEventListener('click', closeDialog);

  loadForm();

  fileInput.addEventListener('change', () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      errorBox.textContent = '请选择图片文件。';
      return;
    }
    if (file.size > 2 * 1024 * 1024) {
      errorBox.textContent = '头像图片不能超过 2 MB。';
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      avatarData = String(reader.result || '');
      errorBox.textContent = '';
      renderAvatar();
    };
    reader.readAsDataURL(file);
  });

  saveButton.addEventListener('click', () => {
    const nickname = nicknameInput.value.trim() || '管理员';
    localStorage.setItem(nicknameKey, nickname);
    if (avatarData) {
      localStorage.setItem(avatarKey, avatarData);
    } else {
      localStorage.removeItem(avatarKey);
    }
    if (dialog) {
      closeDialog();
      window.dispatchEvent(new CustomEvent('iot:admin-profile-updated'));
    } else {
      window.location.href = '/dashboard';
    }
  });
})();
