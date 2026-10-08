"""任务 4：认领与取消授课的规则测试。"""

import pytest

from app.errors import BusinessError
from app.models import Offering, OfferingStatus, TeacherQualification
from app.services.teaching import claim_offering, list_my_offerings, release_offering


def _offering_in_closed_semester(db, seed_basic, *, teacher_id=None) -> Offering:
    offering = Offering(
        semester_id=seed_basic.closed_semester.id,
        course_id=seed_basic.courses["algorithm"].id,
        teacher_id=teacher_id,
        section_number="01",
        capacity=10,
    )
    db.add(offering)
    db.commit()
    return offering


def _qualify(db, teacher, course):
    db.add(TeacherQualification(teacher_id=teacher.id, course_id=course.id))
    db.commit()


def test_claim_assigns_teacher_and_appears_in_my_offerings(db, seed_basic):
    algorithm = seed_basic.offerings["algorithm"]
    teacher = seed_basic.teachers["t1"]

    claim_offering(db, teacher_id=teacher.id, offering_id=algorithm.id)

    assert algorithm.teacher_id == teacher.id
    assert [
        item["offering_id"] for item in list_my_offerings(db, teacher_id=teacher.id)
    ] == [algorithm.id]


def test_claim_requires_qualification(db, seed_basic):
    network = seed_basic.offerings["network"]
    teacher = seed_basic.teachers["t1"]

    with pytest.raises(BusinessError) as error:
        claim_offering(db, teacher_id=teacher.id, offering_id=network.id)

    assert error.value.status_code == 403
    assert "资格" in error.value.message
    assert network.teacher_id is None


def test_claim_rejects_section_owned_by_another_teacher(db, seed_basic):
    database = seed_basic.offerings["database"]
    teacher = seed_basic.teachers["t1"]
    _qualify(db, teacher, seed_basic.courses["database"])

    with pytest.raises(BusinessError) as error:
        claim_offering(db, teacher_id=teacher.id, offering_id=database.id)

    assert error.value.status_code == 409
    assert "其他教师" in error.value.message
    assert database.teacher_id == seed_basic.teachers["t2"].id


def test_claim_rejects_teacher_time_conflict(db, seed_basic):
    """算法与网络的上课时间相同，已承担其一的教师不能再承担另一个。"""
    algorithm = seed_basic.offerings["algorithm"]
    network = seed_basic.offerings["network"]
    teacher = seed_basic.teachers["t1"]
    _qualify(db, teacher, seed_basic.courses["network"])

    claim_offering(db, teacher_id=teacher.id, offering_id=algorithm.id)
    with pytest.raises(BusinessError) as error:
        claim_offering(db, teacher_id=teacher.id, offering_id=network.id)

    assert error.value.status_code == 409
    assert "冲突" in error.value.message
    assert network.teacher_id is None


def test_claim_is_idempotent_for_own_section(db, seed_basic):
    algorithm = seed_basic.offerings["algorithm"]
    teacher = seed_basic.teachers["t1"]

    claim_offering(db, teacher_id=teacher.id, offering_id=algorithm.id)
    claim_offering(db, teacher_id=teacher.id, offering_id=algorithm.id)

    assert algorithm.teacher_id == teacher.id
    assert len(list_my_offerings(db, teacher_id=teacher.id)) == 1


def test_claim_rejects_cancelled_section(db, seed_basic):
    algorithm = seed_basic.offerings["algorithm"]
    algorithm.status = OfferingStatus.CANCELLED
    db.commit()
    teacher = seed_basic.teachers["t1"]

    with pytest.raises(BusinessError) as error:
        claim_offering(db, teacher_id=teacher.id, offering_id=algorithm.id)

    assert error.value.status_code == 409
    assert "取消" in error.value.message


def test_claim_rejects_closed_semester(db, seed_basic):
    offering = _offering_in_closed_semester(db, seed_basic)
    teacher = seed_basic.teachers["t1"]

    with pytest.raises(BusinessError) as error:
        claim_offering(db, teacher_id=teacher.id, offering_id=offering.id)

    assert error.value.status_code == 409
    assert "关闭" in error.value.message
    assert offering.teacher_id is None


def test_claim_rejects_inactive_teacher(db, seed_basic):
    algorithm = seed_basic.offerings["algorithm"]
    teacher = seed_basic.teachers["t1"]
    teacher.active = False
    db.commit()

    with pytest.raises(BusinessError) as error:
        claim_offering(db, teacher_id=teacher.id, offering_id=algorithm.id)

    assert error.value.status_code == 403
    assert "停用" in error.value.message


def test_claim_rejects_unknown_teacher_or_offering(db, seed_basic):
    algorithm = seed_basic.offerings["algorithm"]
    teacher = seed_basic.teachers["t1"]

    with pytest.raises(BusinessError) as missing_teacher:
        claim_offering(db, teacher_id=9999, offering_id=algorithm.id)
    assert missing_teacher.value.status_code == 404

    with pytest.raises(BusinessError) as missing_offering:
        claim_offering(db, teacher_id=teacher.id, offering_id=9999)
    assert missing_offering.value.status_code == 404


def test_release_clears_teacher_and_restores_claimable_state(db, seed_basic):
    algorithm = seed_basic.offerings["algorithm"]
    teacher = seed_basic.teachers["t1"]
    claim_offering(db, teacher_id=teacher.id, offering_id=algorithm.id)

    release_offering(db, teacher_id=teacher.id, offering_id=algorithm.id)

    assert algorithm.teacher_id is None
    assert list_my_offerings(db, teacher_id=teacher.id) == []


def test_release_rejects_section_of_another_teacher(db, seed_basic):
    database = seed_basic.offerings["database"]
    teacher = seed_basic.teachers["t1"]

    with pytest.raises(BusinessError) as error:
        release_offering(db, teacher_id=teacher.id, offering_id=database.id)

    assert error.value.status_code == 403
    assert "只能取消自己承担的班次" == error.value.message
    assert database.teacher_id == seed_basic.teachers["t2"].id


def test_release_rejects_section_without_teacher(db, seed_basic):
    algorithm = seed_basic.offerings["algorithm"]
    teacher = seed_basic.teachers["t1"]

    with pytest.raises(BusinessError) as error:
        release_offering(db, teacher_id=teacher.id, offering_id=algorithm.id)

    assert error.value.status_code == 403
    assert "无人承担" in error.value.message


def test_release_rejects_closed_semester(db, seed_basic):
    teacher = seed_basic.teachers["t1"]
    offering = _offering_in_closed_semester(db, seed_basic, teacher_id=teacher.id)

    with pytest.raises(BusinessError) as error:
        release_offering(db, teacher_id=teacher.id, offering_id=offering.id)

    assert error.value.status_code == 409
    assert offering.teacher_id == teacher.id
