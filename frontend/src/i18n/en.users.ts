/**
 * English strings for user management (Users list/form, set-password panel) and
 * the change-password section of "My profile".
 * Merged into the English bundle in i18n.ts.
 *
 * Namespace owned by this file: usermgmt.
 */
export const enUsers = {
  usermgmt: {
    nav: "Users",
    title: "Users",
    singular: "User",
    username: "Username",
    username_help: "Cannot be changed after the user is created.",
    email: "Email",
    first_name: "First name",
    last_name: "Last name",
    full_name: "Name",
    language: "Language",
    password: "Initial password",
    password_help: "Checked against the password policy. The user can change it from My profile.",
    is_active: "Active",
    last_login: "Last login",
    never: "Never",
    roles: "Roles",
    roles_help: "Pick the roles this user holds. You cannot change your own roles.",
    roles_empty: "No roles are defined yet.",
    roles_load_failed: "Roles could not be loaded.",
    active: "Active",
    inactive: "Inactive",
    superuser: "Superuser",
    deactivate: "Deactivate",
    activate: "Activate",
    // Set-password panel (admin)
    set_password: {
      title: "Set password",
      help: "Replaces this user's password. Share the new password with them securely.",
      new_password: "New password",
      confirm_password: "Confirm new password",
      mismatch: "The passwords do not match.",
      submit: "Set password",
      done: "Password updated.",
      failed: "The password could not be updated.",
    },
    // Change-password section (My profile)
    change_password: {
      title: "Change password",
      help: "Enter your current password, then a new one.",
      old_password: "Current password",
      new_password: "New password",
      confirm_password: "Confirm new password",
      mismatch: "The passwords do not match.",
      submit: "Change password",
      submitting: "Changing...",
      done: "Password changed.",
      failed: "The password could not be changed.",
    },
    lang: {
      en: "English",
      hi: "Hindi",
      ta: "Tamil",
      pa: "Punjabi",
      gu: "Gujarati",
      or: "Odia",
      bn: "Bengali",
      kn: "Kannada",
      as: "Assamese",
      te: "Telugu",
      ml: "Malayalam",
      mr: "Marathi",
      ur: "Urdu",
    },
  },
};
