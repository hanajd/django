"""
初始化数据管理命令
创建初始角色和菜单数据
"""
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from apps.core.models import Role, Menu, UserProfile


class Command(BaseCommand):
    help = '初始化角色和菜单数据'

    def handle(self, *args, **options):
        self.stdout.write('开始初始化数据...')
        
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
                self.stdout.write(f'创建角色: {role.name}')
            else:
                self.stdout.write(f'角色已存在: {role.name}')
        
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
                'icon': 'users',
                'sort_order': 3,
                'roles': [super_admin, admin]
            },
            {
                'name': '角色管理',
                'path': '/roles/',
                'icon': 'shield',
                'sort_order': 4,
                'roles': [super_admin, admin]
            },
            {
                'name': '菜单管理',
                'path': '/menus/',
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
                self.stdout.write(f'创建菜单: {menu.name}')
            else:
                self.stdout.write(f'菜单已存在: {menu.name}')
        
        database_management = Menu.objects.filter(name='数据库管理', parent__isnull=True).first()
        user_management = Menu.objects.filter(name='用户管理', parent__isnull=True).first()
        file_parent_menu = Menu.objects.filter(name='文件与提取', parent__isnull=True).first()

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
                    self.stdout.write(f'创建子菜单: {menu.name}')
                else:
                    self.stdout.write(f'子菜单已存在: {menu.name}')
        
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
                    self.stdout.write(f'创建子菜单: {menu.name}')
                else:
                    self.stdout.write(f'子菜单已存在: {menu.name}')
        
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
                    'name': '流程处理',
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
                    self.stdout.write(f'创建子菜单: {menu.name}')
                else:
                    self.stdout.write(f'子菜单已存在: {menu.name}')
        
        # 为超级管理员用户分配角色
        try:
            superuser = User.objects.get(is_superuser=True)
            profile, created = UserProfile.objects.get_or_create(
                user=superuser,
                defaults={'role': super_admin}
            )
            if created:
                self.stdout.write(f'为超级管理员 {superuser.username} 分配角色')
            else:
                if profile.role != super_admin:
                    profile.role = super_admin
                    profile.save()
                    self.stdout.write(f'更新超级管理员 {superuser.username} 的角色')
        except User.DoesNotExist:
            self.stdout.write('未找到超级管理员用户')
        
        self.stdout.write(self.style.SUCCESS('数据初始化完成！'))
