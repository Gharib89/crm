function validate(ctx) {
  var name = ctx.getFormContext().getAttribute('new_name');
  return name.getValue() !== null;
}
