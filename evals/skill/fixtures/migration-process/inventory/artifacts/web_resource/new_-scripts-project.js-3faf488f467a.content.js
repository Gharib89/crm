var Project = { onLoad: function (ctx) {
  return Xrm.Page.getAttribute('new_name').getValue();
} };
