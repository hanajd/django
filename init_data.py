r"""
初始化数据脚本
运行方式: C:\Users\22376\AppData\Local\Programs\Python\Python313\python.exe init_data.py
"""
import os
import django

# 设置 Django 环境
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tablet_backend.settings')
django.setup()

from django.contrib.auth.models import User
from apps.core.models import Role, Menu, UserProfile

print('开始初始化数据...')

# 创建角色
roles_data = [
    {
        'name': '超级管理员',
        'code': 'super_admin',
        'description': '系统最高权限管理员，可以访问所有功能'
    },
    {
        'name': '普通管理员',
        'code': 'admin',
        'description': '普通管理员，可以管理用户和角色'
    },
    {
        'name': 'App 用户',
        'code': 'app_user',
        'description': '平板 App 端用户'
    }
]

for role_data in roles_data:
    role, created = Role.objects.get_or_create(
        code=role_data['code'],
        defaults={
            'name': role_data['name'],
            'description': role_data['description']
        }
    )
    if created:
        print(f'创建角色: {role.name}')
    else:
        print(f'角色已存在: {role.name}')

# 获取角色
super_admin = Role.objects.get(code='super_admin')
admin = Role.objects.get(code='admin')
app_user = Role.objects.get(code='app_user')

# 创建菜单
menus_data = [
    {
        'name': '仪表盘',
        'path': '/',
        'icon': 'dashboard',
        'sort_order': 1,
        'roles': [super_admin, admin, app_user]
    },
    {
        'name': '数据库管理',
        'path': '',
        'icon': 'database-2',
        'sort_order': 2,
        'roles': [super_admin, admin]
    },
    {
        'name': '用户管理',
        'path': '',
        'icon': 'team',
        'sort_order': 3,
        'roles': [super_admin, admin]
    },
    {
        'name': '角色管理',
        'path': '',
        'icon': 'shield',
        'sort_order': 4,
        'roles': [super_admin, admin]
    },
    {
        'name': '菜单管理',
        'path': '',
        'icon': 'menu',
        'sort_order': 5,
        'roles': [super_admin]
    },
    {
        'name': '文件与提取',
        'path': '',
        'icon': 'folder',
        'sort_order': 6,
        'roles': [super_admin, admin, app_user]
    },
]

# 创建顶级菜单
user_management = None
database_management = None
role_management = None
menu_management = None
file_parent_menu = None
for menu_data in menus_data:
    menu, created = Menu.objects.get_or_create(
        name=menu_data['name'],
        defaults={
            'path': menu_data['path'],
            'icon': menu_data['icon'],
            'sort_order': menu_data['sort_order']
        }
    )
    menu.roles.set(menu_data['roles'])
    menu.save()
    
    if created:
        print(f'创建菜单: {menu.name}')
    else:
        print(f'菜单已存在: {menu.name}')
        # 更新现有菜单的path
        if menu.name in ['用户管理', '角色管理', '菜单管理']:
            menu.path = ''
            menu.save()
    
    # 无论如何都获取父菜单对象
    if menu.name == '用户管理':
        user_management = menu
    elif menu.name == '数据库管理':
        database_management = menu
    elif menu.name == '角色管理':
        role_management = menu
    elif menu.name == '菜单管理':
        menu_management = menu
    elif menu.name == '文件与提取':
        file_parent_menu = menu

# 创建用户管理的子菜单
if database_management:
    database_menu_data = [
        {
            'name': '检测仪器',
            'path': '/database/devices/',
            'icon': 'database-2',
            'sort_order': 1,
            'parent': database_management,
            'roles': [super_admin, admin]
        }
    ]

    for menu_data in database_menu_data:
        menu, created = Menu.objects.get_or_create(
            name=menu_data['name'],
            parent=menu_data['parent'],
            defaults={
                'path': menu_data['path'],
                'icon': menu_data['icon'],
                'sort_order': menu_data['sort_order']
            }
        )
        menu.roles.set(menu_data['roles'])
        menu.save()

        if created:
            print(f'创建子菜单: {menu.name}')
        else:
            print(f'子菜单已存在: {menu.name}')

# 创建用户管理的子菜单
if user_management:
    user_menu_data = [
        {
            'name': '用户列表',
            'path': '/users/',
            'icon': 'list',
            'sort_order': 1,
            'parent': user_management,
            'roles': [super_admin, admin]
        }
    ]
    
    for menu_data in user_menu_data:
        menu, created = Menu.objects.get_or_create(
            name=menu_data['name'],
            parent=menu_data['parent'],
            defaults={
                'path': menu_data['path'],
                'icon': menu_data['icon'],
                'sort_order': menu_data['sort_order']
            }
        )
        menu.roles.set(menu_data['roles'])
        menu.save()
        
        if created:
            print(f'创建子菜单: {menu.name}')
        else:
            print(f'子菜单已存在: {menu.name}')

# 创建角色管理的子菜单
if role_management:
    role_menu_data = [
        {
            'name': '角色列表',
            'path': '/roles/',
            'icon': 'list',
            'sort_order': 1,
            'parent': role_management,
            'roles': [super_admin, admin]
        }
    ]
    
    for menu_data in role_menu_data:
        menu, created = Menu.objects.get_or_create(
            name=menu_data['name'],
            parent=menu_data['parent'],
            defaults={
                'path': menu_data['path'],
                'icon': menu_data['icon'],
                'sort_order': menu_data['sort_order']
            }
        )
        menu.roles.set(menu_data['roles'])
        menu.save()
        
        if created:
            print(f'创建子菜单: {menu.name}')
        else:
            print(f'子菜单已存在: {menu.name}')

# 创建菜单管理的子菜单
if menu_management:
    menu_menu_data = [
        {
            'name': '菜单列表',
            'path': '/menus/',
            'icon': 'list',
            'sort_order': 1,
            'parent': menu_management,
            'roles': [super_admin]
        }
    ]
    
    for menu_data in menu_menu_data:
        menu, created = Menu.objects.get_or_create(
            name=menu_data['name'],
            parent=menu_data['parent'],
            defaults={
                'path': menu_data['path'],
                'icon': menu_data['icon'],
                'sort_order': menu_data['sort_order']
            }
        )
        menu.roles.set(menu_data['roles'])
        menu.save()
        
        if created:
            print(f'创建子菜单: {menu.name}')
        else:
            print(f'子菜单已存在: {menu.name}')

# 创建文件与提取的子菜单
if file_parent_menu:
    file_menu_data = [
        {
            'name': '文件库',
            'path': '/files/',
            'icon': 'file-list',
            'sort_order': 1,
            'parent': file_parent_menu,
            'roles': [super_admin, admin, app_user],
        },
        {
            'name': 'OCR处理',
            'path': '/files/process/',
            'icon': 'cpu',
            'sort_order': 2,
            'parent': file_parent_menu,
            'roles': [super_admin, admin, app_user],
        },
    ]
    for menu_data in file_menu_data:
        menu, created = Menu.objects.get_or_create(
            name=menu_data['name'],
            parent=menu_data['parent'],
            defaults={
                'path': menu_data['path'],
                'icon': menu_data['icon'],
                'sort_order': menu_data['sort_order'],
            },
        )
        menu.roles.set(menu_data['roles'])
        menu.save()
        if created:
            print(f'创建子菜单: {menu.name}')
        else:
            print(f'子菜单已存在: {menu.name}')

# 为超级管理员用户分配角色
try:
    superuser = User.objects.get(is_superuser=True)
    profile, created = UserProfile.objects.get_or_create(
        user=superuser,
        defaults={'role': super_admin}
    )
    if created:
        print(f'为超级管理员 {superuser.username} 分配角色')
    else:
        if profile.role != super_admin:
            profile.role = super_admin
            profile.save()
            print(f'更新超级管理员 {superuser.username} 的角色')
except User.DoesNotExist:
    print('未找到超级管理员用户')

print('数据初始化完成！')
