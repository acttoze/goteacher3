<?php
// zz_readonly.extend.php 판정 시험(구 사이트 없이). php test_readonly.php <extend 파일>
define('_GNUBOARD_', true);
define('G5_DATA_PATH', sys_get_temp_dir() . '/g7ro-none');  // 플래그 없음 → 아래 include는 아무것도 안 함
function alert($m, $u) { echo "ALERT\n"; exit; }
include $argv[1];
$cases = array(
    array('login_check.php', 'POST', false), array('logout.php', 'GET', false), array('password_check.php', 'POST', false),
    array('board.php', 'GET', false), array('index.php', 'GET', false), array('download.php', 'GET', false),
    array('ajax.meal_widget.php', 'GET', false), array('view_image.php', 'HEAD', false),
    array('write_update.php', 'POST', true), array('go_write_update.php', 'POST', true), array('ajax.memo.php', 'POST', true),
    array('board.php', 'POST', true), array('ajax.member_search.php', 'POST', true),
    array('write.php', 'GET', true), array('delete.php', 'GET', true), array('delete_comment.php', 'GET', true),
    array('go_delete_comment.php', 'GET', true), array('good.php', 'GET', true), array('db.php', 'GET', true),
    array('ajax.work.php', 'GET', true), array('ajax.add_contact_group.php', 'GET', true), array('ajax.autosavedel.php', 'GET', true),
    array('register_form.php', 'GET', true), array('member_leave.php', 'GET', true), array('school_sign.php', 'GET', true),
);
$bad = 0;
foreach ($cases as $c) {
    $got = g7_readonly_blocked($c[0], $c[1]);
    if ($got !== $c[2]) { $bad++; echo "틀림 {$c[1]} {$c[0]}\n"; }
}
echo count($cases) . "건 중 틀림 $bad\n";
exit($bad ? 1 : 0);
